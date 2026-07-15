from __future__ import annotations
from http.cookies import SimpleCookie
import argparse
import json
import logging
import os
import smtplib
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv


SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")


class ElectricityMonitorError(RuntimeError):
    """电量监控基础异常。"""


class VerificationRequiredError(ElectricityMonitorError):
    """Cookie 失效或请求被真人检测页面拦截。"""


class InvalidResponseError(ElectricityMonitorError):
    """接口返回内容不是预期的电量 XML。"""


@dataclass(frozen=True)
class ElectricityRecord:
    recorded_at: datetime
    recorded_at_raw: str
    remain: Decimal


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ElectricityMonitorError(f"环境变量 {name} 必须是整数，当前值为 {raw!r}") from exc
    if value < minimum:
        raise ElectricityMonitorError(f"环境变量 {name} 不能小于 {minimum}")
    return value

def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()

    if not raw:
        return default

    if raw in {"1", "true", "yes", "on"}:
        return True

    if raw in {"0", "false", "no", "off"}:
        return False

    raise ElectricityMonitorError(
        f"环境变量 {name} 必须是 true/false，当前值为 {raw!r}"
    )

def env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, "").strip() or default
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ElectricityMonitorError(f"环境变量 {name} 必须是数字，当前值为 {raw!r}") from exc


def resolve_local_path(env_name: str, default: str) -> Path:
    raw = os.getenv(env_name, "").strip() or default
    path = Path(raw)
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def configure_logging() -> logging.Logger:
    log_path = resolve_local_path("LOG_FILE", "./logs/electricity-monitor.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("electricity_monitor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


LOGGER = configure_logging()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_record_time(raw: str) -> datetime:
    raw = raw.strip()
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidResponseError(f"无法解析电量记录时间：{raw!r}") from exc


def child_text(element: ET.Element, name: str) -> str | None:
    for child in list(element):
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return None

def is_verification_page(text: str) -> bool:
    lowered = text.lower()

    return (
        "真人检测" in text
        or "自动检测请求" in text
        or "detect-human" in lowered
        or "/wengine/auth/" in lowered
    )

def parse_electricity_xml(xml_text: str) -> list[ElectricityRecord]:
    stripped = xml_text.lstrip()
    lowered = stripped[:1000].lower()

    if is_verification_page(stripped):
        raise VerificationRequiredError(
            "接口返回了真人检测页面，需要刷新浏览器会话。"
        )

    if "<html" in lowered:
        raise InvalidResponseError(
            "接口返回了 HTML 页面，而不是预期的电量 XML。"
        )

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise InvalidResponseError("接口返回内容不是合法 XML") from exc

    records: list[ElectricityRecord] = []
    for element in root.iter():
        if local_name(element.tag) != "ds":
            continue
        raw_date = child_text(element, "rdate")
        raw_remain = child_text(element, "remain")
        if not raw_date or raw_remain is None:
            continue
        try:
            remain = Decimal(raw_remain)
        except InvalidOperation:
            continue
        records.append(
            ElectricityRecord(
                recorded_at=parse_record_time(raw_date),
                recorded_at_raw=raw_date,
                remain=remain,
            )
        )

    if not records:
        raise InvalidResponseError("XML 中未找到包含 rdate 和 remain 的 ds 记录")

    return records


def latest_record(records: Iterable[ElectricityRecord]) -> ElectricityRecord:
    items = list(records)
    if not items:
        raise InvalidResponseError("没有可用电量记录")
    return max(items, key=lambda item: item.recorded_at)

def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    """把 .env 中的 Cookie 请求头解析成字典。"""
    if not cookie_header.strip():
        return {}

    parsed = SimpleCookie()
    parsed.load(cookie_header)

    return {
        name: morsel.value
        for name, morsel in parsed.items()
    }


def load_saved_browser_cookies(path: Path) -> dict[str, str]:
    """读取 Playwright 刷新后保存的 Cookie。"""
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("浏览器 Cookie 文件读取失败：%s", exc)
        return {}

    if not isinstance(data, list):
        return {}

    result: dict[str, str] = {}

    for item in data:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        domain = str(item.get("domain", "")).lstrip(".").lower()

        if not name or not value:
            continue

        if domain == "sdcx.guet.edu.cn" or domain.endswith(
            ".sdcx.guet.edu.cn"
        ):
            result[name] = value

    return result


def get_effective_cookie_header() -> str:
    """
    合并 .env 中的初始 Cookie 和浏览器最近保存的 Cookie。

    浏览器刷新得到的 Cookie 优先级更高。
    """
    cookie_file = resolve_local_path(
        "GUET_COOKIE_FILE",
        "./data/guet-cookies.json",
    )

    cookies = parse_cookie_header(
        os.getenv("GUET_ELECTRICITY_COOKIE", "")
    )
    cookies.update(load_saved_browser_cookies(cookie_file))

    return "; ".join(
        f"{name}={value}"
        for name, value in cookies.items()
    )


def save_browser_cookies(
    path: Path,
    cookies: list[dict],
) -> None:
    """保存浏览器会话 Cookie，供后续 requests 查询使用。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)

def build_session(cookie: str) -> requests.Session:
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
        ),
        "Accept": "*/*",
        "Accept-Language": (
            "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Referer": "http://sdcx.guet.edu.cn/yd",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }

    if cookie:
        headers["Cookie"] = cookie

    session.headers.update(headers)
    return session


def fetch_latest_record_with_requests(
    endpoint: str,
    room_no: str,
    query_count: int,
    timeout: int,
    retries: int,
    cookie: str,
) -> ElectricityRecord:
    """优先使用轻量的 requests 查询电量。"""
    session = build_session(cookie)
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                endpoint,
                params={
                    "roomno": room_no,
                    "n": query_count,
                },
                timeout=timeout,
            )
            response.raise_for_status()

            records = parse_electricity_xml(response.text)
            return latest_record(records)

        except VerificationRequiredError:
            # 真人检测不是普通网络错误，无需重复发送相同请求。
            raise

        except (requests.RequestException, InvalidResponseError) as exc:
            last_error = exc

            if attempt < retries:
                wait_seconds = min(5 * attempt, 15)

                LOGGER.warning(
                    "第 %s/%s 次请求失败：%s；%s 秒后重试",
                    attempt,
                    retries,
                    exc,
                    wait_seconds,
                )
                time.sleep(wait_seconds)

    raise ElectricityMonitorError(
        f"requests 查询在 {retries} 次尝试后仍失败：{last_error}"
    )


def page_requires_verification(page) -> bool:
    """判断当前页面是否仍处于真人检测状态。"""
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""

    current_url = page.url.lower()

    return (
        is_verification_page(body_text)
        or "/wengine/auth/" in current_url
    )


def complete_browser_verification(
    page,
    base_url: str,
    wait_seconds: int,
    navigation_timeout_ms: int,
    reload_delay_seconds: int,
) -> None:
    """
    在隐藏的 Chrome 中完成验证。

    真人检测脚本执行后，网站不一定会可靠地自动跳回 /yd，
    因此程序会等待检测脚本写入 Cookie，然后主动重新访问 /yd。
    """
    target_url = f"{base_url}/yd"
    deadline = time.monotonic() + wait_seconds
    attempt = 0

    page.goto(
        target_url,
        wait_until="domcontentloaded",
        timeout=navigation_timeout_ms,
    )

    while time.monotonic() < deadline:
        attempt += 1

        if not page_requires_verification(page):
            # 即使已经离开检测页，也显式回到目标页面，
            # 确保后续接口请求使用完整验证后的会话。
            if page.url.rstrip("/").lower() != target_url.rstrip("/").lower():
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=navigation_timeout_ms,
                )

            if not page_requires_verification(page):
                LOGGER.info(
                    "浏览器验证完成，当前页面已重定向到 %s",
                    target_url,
                )
                return

        LOGGER.info(
            "真人检测尚未完成，第 %s 次等待；%s 秒后重新访问 %s",
            attempt,
            reload_delay_seconds,
            target_url,
        )

        # 让 detect-human.js 有足够时间执行并写入验证 Cookie。
        page.wait_for_timeout(reload_delay_seconds * 1000)

        # 网站有时不会自动跳转，因此主动重新进入 /yd。
        page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms,
        )

        page.wait_for_timeout(1000)

    raise VerificationRequiredError(
        f"隐藏浏览器在 {wait_seconds} 秒内未能完成真人检测。"
        "网站可能启用了必须人工操作的交互验证。"
    )


def fetch_latest_record_with_browser(
    base_url: str,
    endpoint: str,
    room_no: str,
    query_count: int,
    timeout: int,
) -> ElectricityRecord:
    """Cookie 失效时，通过持久化 Chrome 会话刷新验证状态。"""
    try:
        from playwright.sync_api import (
            Error as PlaywrightError,
            TimeoutError as PlaywrightTimeoutError,
            sync_playwright,
        )
    except ImportError as exc:
        raise ElectricityMonitorError(
            "未安装 Playwright，请执行："
            "python -m pip install playwright"
        ) from exc

    profile_dir = resolve_local_path(
        "GUET_BROWSER_PROFILE_DIR",
        "./data/guet-browser-profile",
    )
    cookie_file = resolve_local_path(
        "GUET_COOKIE_FILE",
        "./data/guet-cookies.json",
    )

    browser_channel = os.getenv(
        "GUET_BROWSER_CHANNEL",
        "chrome",
    ).strip()

    headless = env_bool(
        "GUET_BROWSER_HEADLESS",
        True,
    )

    verification_wait = env_int(
        "GUET_VERIFICATION_WAIT_SECONDS",
        90,
        minimum=10,
    )

    reload_delay_seconds = env_int(
        "GUET_VERIFICATION_RELOAD_DELAY_SECONDS",
        7,
        minimum=5,
    )

    profile_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.warning(
        "Cookie 已失效，正在启动隐藏的 Chrome 刷新会话。"
    )

    try:
        with sync_playwright() as playwright:
            launch_options = {
                "user_data_dir": str(profile_dir),
                "headless": headless,
            }

            if browser_channel:
                launch_options["channel"] = browser_channel

            context = playwright.chromium.launch_persistent_context(
                **launch_options
            )

            try:
                page = (
                    context.pages[0]
                    if context.pages
                    else context.new_page()
                )

                complete_browser_verification(
                    page=page,
                    base_url=base_url,
                    wait_seconds=verification_wait,
                    navigation_timeout_ms=timeout * 1000,
                    reload_delay_seconds=reload_delay_seconds,
                )

                # BrowserContext 自带的 APIRequestContext 与浏览器共享 Cookie。
                api_response = context.request.get(
                    endpoint,
                    params={
                        "roomno": room_no,
                        "n": query_count,
                    },
                    headers={
                        "Accept": "*/*",
                        "Referer": f"{base_url}/yd",
                        "Cache-Control": "no-cache",
                    },
                    timeout=timeout * 1000,
                )

                if not api_response.ok:
                    raise ElectricityMonitorError(
                        "浏览器会话请求电量接口失败："
                        f"HTTP {api_response.status}"
                    )

                response_text = api_response.text()

                # 保存最新 Cookie。以后优先用 requests，不必每次打开浏览器。
                browser_cookies = context.cookies()
                save_browser_cookies(
                    cookie_file,
                    browser_cookies,
                )

                records = parse_electricity_xml(response_text)
                record = latest_record(records)

                LOGGER.info(
                    "浏览器会话刷新成功，新的 Cookie 已保存"
                )
                return record

            finally:
                context.close()

    except PlaywrightTimeoutError as exc:
        raise ElectricityMonitorError(
            f"浏览器访问校园电量页面超时：{exc}"
        ) from exc

    except PlaywrightError as exc:
        raise ElectricityMonitorError(
            f"浏览器自动化执行失败：{exc}"
        ) from exc


def fetch_latest_record() -> ElectricityRecord:
    """
    混合查询方案：

    1. 优先使用 requests + 已保存 Cookie；
    2. 返回真人检测页面时启动 Edge；
    3. 浏览器正常完成检测并保存新 Cookie；
    4. 后续运行重新使用 requests。
    """
    base_url = os.getenv(
        "GUET_BASE_URL",
        "http://sdcx.guet.edu.cn",
    ).strip().rstrip("/")

    room_no = os.getenv(
        "GUET_ROOM_NO",
        "y503616",
    ).strip()

    query_count = env_int(
        "GUET_QUERY_COUNT",
        10,
        minimum=1,
    )

    timeout = env_int(
        "GUET_REQUEST_TIMEOUT",
        20,
        minimum=1,
    )

    retries = env_int(
        "GUET_REQUEST_RETRIES",
        3,
        minimum=1,
    )

    if not room_no:
        raise ElectricityMonitorError(
            "GUET_ROOM_NO 不能为空"
        )

    endpoint = (
        f"{base_url}/yktserver/ecardserv/"
        "ykt.asmx/GetYDLSByRoomno"
    )

    cookie_header = get_effective_cookie_header()

    if cookie_header:
        try:
            return fetch_latest_record_with_requests(
                endpoint=endpoint,
                room_no=room_no,
                query_count=query_count,
                timeout=timeout,
                retries=retries,
                cookie=cookie_header,
            )

        except VerificationRequiredError:
            LOGGER.warning(
                "requests 使用的 Cookie 已失效，"
                "切换到浏览器会话刷新模式"
            )
    else:
        LOGGER.warning(
            "未找到可用 Cookie，直接启动浏览器会话"
        )

    return fetch_latest_record_with_browser(
        base_url=base_url,
        endpoint=endpoint,
        room_no=room_no,
        query_count=query_count,
        timeout=timeout,
    )


def read_state(path: Path) -> dict:
    if not path.exists():
        return {
            "last_success_date": None,
            "last_checked_at": None,
            "last_recorded_at": None,
            "last_remain": None,
            "low_power_alerted": False,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("状态文件读取失败，将使用空状态：%s", path)
        return {}


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


FIXED_MAIL_SUBJECT = "[宿舍电量监控] 低电量提醒"


def qq_mail_configured() -> bool:
    required = ("QQ_EMAIL", "QQ_AUTH_CODE", "ALERT_TO_EMAIL")
    return all(os.getenv(name, "").strip() for name in required)


def send_low_power_email(record: ElectricityRecord, threshold: Decimal) -> None:
    username = os.getenv("QQ_EMAIL", "").strip()
    auth_code = os.getenv("QQ_AUTH_CODE", "").strip()
    recipient = os.getenv("ALERT_TO_EMAIL", "").strip()
    smtp_host = os.getenv("QQ_SMTP_HOST", "smtp.qq.com").strip()
    smtp_port = env_int("QQ_SMTP_PORT", 465, minimum=1)
    smtp_timeout = env_int("QQ_SMTP_TIMEOUT", 30, minimum=1)
    room_no = os.getenv("GUET_ROOM_NO", "y503616").strip()

    if not qq_mail_configured():
        raise ElectricityMonitorError(
            "邮件提醒未配置完整：请设置 QQ_EMAIL、QQ_AUTH_CODE 和 ALERT_TO_EMAIL"
        )

    message = EmailMessage()
    # 主题保持完全固定，便于在 QQ 邮箱中按主题创建过滤规则。
    message["Subject"] = FIXED_MAIL_SUBJECT
    message["From"] = username
    message["To"] = recipient
    message.set_content(
        "\n".join(
            [
                "宿舍剩余电量提醒",
                "",
                f"房间：{room_no}",
                f"当前剩余电量：{record.remain}",
                f"提醒阈值：{threshold}",
                f"电量记录时间：{record.recorded_at_raw}",
                f"检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "剩余电量已经低于设定阈值，请及时充值。",
            ]
        )
    )

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=smtp_timeout) as smtp:
            smtp.login(username, auth_code)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise ElectricityMonitorError(f"QQ 邮箱发送失败：{exc}") from exc


def run(*, check_only: bool, force: bool) -> int:
    state_path = resolve_local_path("STATE_FILE", "./data/state.json")
    state = read_state(state_path)
    today = datetime.now().date().isoformat()

    if not check_only and not force and state.get("last_success_date") == today:
        LOGGER.info("今天已成功检查过电量，本次补偿任务无需重复请求")
        return 0

    record = fetch_latest_record()
    room_no = os.getenv("GUET_ROOM_NO", "y503616").strip()
    threshold = env_decimal("GUET_ELECTRICITY_THRESHOLD", "20")

    LOGGER.info(
        "房间=%s，记录时间=%s，剩余电量=%s",
        room_no,
        record.recorded_at_raw,
        record.remain,
    )
    print(
        json.dumps(
            {
                "room_no": room_no,
                "recorded_at": record.recorded_at_raw,
                "remain": str(record.remain),
                "threshold": str(threshold),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if check_only:
        return 0

    state.update(
        {
            "last_success_date": today,
            "last_checked_at": datetime.now().isoformat(timespec="seconds"),
            "last_recorded_at": record.recorded_at_raw,
            "last_remain": str(record.remain),
        }
    )

    already_alerted = bool(state.get("low_power_alerted", False))
    if record.remain < threshold:
        if already_alerted:
            LOGGER.info("电量仍低于阈值，但此前已提醒，本次不重复发送邮件")
        elif qq_mail_configured():
            send_low_power_email(record, threshold)
            state["low_power_alerted"] = True
            state["last_alert_at"] = datetime.now().isoformat(timespec="seconds")
            LOGGER.warning("电量低于阈值，提醒邮件已发送")
        else:
            LOGGER.warning(
                "电量低于阈值，但 QQ 邮箱未配置完整；未发送邮件，也不会标记为已提醒"
            )
    else:
        if already_alerted:
            LOGGER.info("电量已恢复到阈值以上，重置低电量提醒状态")
        state["low_power_alerted"] = False

    write_state(state_path, state)
    return 0


SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<DataTable xmlns="http://tempuri.org/">
  <diffgr:diffgram xmlns:msdata="urn:schemas-microsoft-com:xml-msdata"
                   xmlns:diffgr="urn:schemas-microsoft-com:xml-diffgram-v1">
    <NewDataSet xmlns="">
      <ds diffgr:id="ds1" msdata:rowOrder="0">
        <rdate>2026-07-15 00:00:00</rdate>
        <remain>99.90</remain>
      </ds>
    </NewDataSet>
  </diffgr:diffgram>
</DataTable>
"""


def self_test() -> int:
    record = latest_record(parse_electricity_xml(SAMPLE_XML))
    assert record.recorded_at_raw == "2026-07-15 00:00:00"
    assert record.remain == Decimal("99.90")
    print("自检通过：成功解析样例 XML，剩余电量为 99.90")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="桂电宿舍剩余电量监控")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只查询并输出电量，不写状态、不发送邮件",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使今天已成功检查，也强制重新查询",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="仅运行 XML 解析自检，不访问网络",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="发送一封 QQ 邮箱测试邮件，不查询电量、不修改状态",
    )
    args = parser.parse_args()

    try:
        if args.self_test:
            return self_test()
        if args.test_email:
            now = datetime.now()
            record = ElectricityRecord(
                recorded_at=now,
                recorded_at_raw=now.strftime("%Y-%m-%d %H:%M:%S"),
                remain=Decimal("19.90"),
            )
            send_low_power_email(
                record,
                env_decimal("GUET_ELECTRICITY_THRESHOLD", "20"),
            )
            print(f"QQ 邮箱测试邮件已发送，固定主题：{FIXED_MAIL_SUBJECT}")
            return 0
        return run(check_only=args.check_only, force=args.force)
    except VerificationRequiredError as exc:
        LOGGER.error("%s", exc)
        return 3
    except ElectricityMonitorError as exc:
        LOGGER.error("%s", exc)
        return 2
    except Exception:
        LOGGER.exception("发生未处理异常")
        return 1


if __name__ == "__main__":
    sys.exit(main())