from __future__ import annotations

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


def parse_electricity_xml(xml_text: str) -> list[ElectricityRecord]:
    stripped = xml_text.lstrip()
    lowered = stripped[:1000].lower()

    if "<html" in lowered or "真人检测" in stripped or "detect-human" in lowered:
        raise VerificationRequiredError(
            "接口返回了真人检测页面。GUET_ELECTRICITY_COOKIE 可能已失效，需要从 Reqable 更新。"
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


def build_session(cookie: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
            ),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "http://sdcx.guet.edu.cn/yd",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Cookie": cookie,
        }
    )
    return session


def fetch_latest_record() -> ElectricityRecord:
    base_url = os.getenv("GUET_BASE_URL", "http://sdcx.guet.edu.cn").strip().rstrip("/")
    room_no = os.getenv("GUET_ROOM_NO", "y503616").strip()
    query_count = env_int("GUET_QUERY_COUNT", 10, minimum=1)
    timeout = env_int("GUET_REQUEST_TIMEOUT", 20, minimum=1)
    retries = env_int("GUET_REQUEST_RETRIES", 3, minimum=1)
    cookie = os.getenv("GUET_ELECTRICITY_COOKIE", "").strip()

    if not room_no:
        raise ElectricityMonitorError("GUET_ROOM_NO 不能为空")
    if not cookie:
        raise ElectricityMonitorError("未设置 GUET_ELECTRICITY_COOKIE")

    endpoint = (
        f"{base_url}/yktserver/ecardserv/ykt.asmx/GetYDLSByRoomno"
    )
    session = build_session(cookie)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                endpoint,
                params={"roomno": room_no, "n": query_count},
                timeout=timeout,
            )
            response.raise_for_status()
            records = parse_electricity_xml(response.text)
            return latest_record(records)
        except VerificationRequiredError:
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
        f"请求在 {retries} 次尝试后仍失败：{last_error}"
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


def gmail_configured() -> bool:
    required = ("GMAIL_USERNAME", "GMAIL_APP_PASSWORD", "ALERT_TO_EMAIL")
    return all(os.getenv(name, "").strip() for name in required)


def send_low_power_email(record: ElectricityRecord, threshold: Decimal) -> None:
    username = os.getenv("GMAIL_USERNAME", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.getenv("ALERT_TO_EMAIL", "").strip()
    smtp_host = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = env_int("GMAIL_SMTP_PORT", 465, minimum=1)
    room_no = os.getenv("GUET_ROOM_NO", "y503616").strip()

    if not gmail_configured():
        raise ElectricityMonitorError(
            "邮件提醒未配置完整：请设置 GMAIL_USERNAME、GMAIL_APP_PASSWORD 和 ALERT_TO_EMAIL"
        )

    message = EmailMessage()
    message["Subject"] = f"宿舍剩余电量不足：{record.remain}"
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

    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.login(username, app_password)
        smtp.send_message(message)


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
        elif gmail_configured():
            send_low_power_email(record, threshold)
            state["low_power_alerted"] = True
            state["last_alert_at"] = datetime.now().isoformat(timespec="seconds")
            LOGGER.warning("电量低于阈值，提醒邮件已发送")
        else:
            LOGGER.warning(
                "电量低于阈值，但 Gmail 未配置完整；未发送邮件，也不会标记为已提醒"
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
    args = parser.parse_args()

    try:
        if args.self_test:
            return self_test()
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
