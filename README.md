# 宿舍电量监控

这是一个基于 Python 的宿舍电量自动监控项目。程序会定时查询校园电量接口，解析最新剩余电量，并在电量低于设定阈值时通过 QQ 邮箱发送提醒。

邮件主题固定为：

```text
[宿舍电量监控] 低电量提醒
```

因此可以在邮箱中按主题建立过滤规则，将所有低电量提醒自动归类。

## 功能

- 查询宿舍最新剩余电量；
- 解析 ASP.NET DataTable XML；
- 优先使用 `requests + Cookie` 查询；
- Cookie 失效时自动启动 Playwright 浏览器刷新会话；
- 保存刷新后的 Cookie，后续查询不再重复打开浏览器；
- 通过 QQ 邮箱 SMTP 发送低电量提醒；
- 低电量期间只提醒一次，充值后自动重置；
- 支持 Windows 计划任务；
- 支持 Linux 普通用户 `crontab` 定时任务；
- 支持网络失败重试、日志记录和状态持久化。

## 工作流程

```text
requests + 已保存 Cookie
        │
        ├─ 查询成功 → 解析剩余电量
        │
        └─ 返回真人检测页面
                 │
                 ▼
        启动 Playwright 浏览器
                 │
                 ▼
        尝试完成网页验证
                 │
                 ▼
        保存新的 Cookie
                 │
                 ▼
        获取并解析剩余电量
```

浏览器使用程序专用的用户目录，不会占用日常浏览器配置。

## 项目文件

```text
electricity_monitor/
├── electricity_monitor.py
├── requirements.txt
├── environment.yml
├── .env
├── .env.example
├── install_tasks.ps1
├── uninstall_tasks.ps1
├── data/
└── logs/
```

其中：

- `.env`：本机或服务器私密配置，不应提交到 Git；
- `.env.example`：配置模板，不包含真实凭据；
- `data/guet-cookies.json`：浏览器刷新后保存的 Cookie；
- `data/guet-chrome-profile/`：Playwright 专用浏览器用户目录；
- `data/state.json`：上次检查和提醒状态；
- `logs/electricity-monitor.log`：运行日志。

## 环境要求

- Python 3.11；
- Windows 10/11 或常见 Linux 发行版；
- 可访问校园电量查询接口；
- 已开启 SMTP 服务的 QQ 邮箱；
- Cookie 失效时需要可用的 Chrome 或 Playwright Chromium。

## 安装依赖

### Windows：使用 Conda

```powershell
conda create -n electricity-monitor python=3.11 -y
conda activate electricity-monitor
python -m pip install -r requirements.txt
```

已有环境时：

```powershell
conda activate electricity-monitor
python -m pip install -r requirements.txt --upgrade
```

程序默认可调用电脑中已经安装的 Google Chrome，通常不需要额外安装 Chromium。

### Linux：使用虚拟环境

进入服务器上的项目目录：

```bash
cd /path/to/electricity_monitor
```

创建并激活虚拟环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

安装依赖：

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

安装 Playwright Chromium：

```bash
python -m playwright install chromium
```

Ubuntu/Debian 缺少浏览器系统依赖时执行：

```bash
sudo python -m playwright install-deps chromium
```

如果当前 Python 来自虚拟环境或 Conda，建议用解释器的完整路径执行，确保浏览器与运行程序的环境一致。

## 配置环境变量

复制模板。

Windows：

```powershell
Copy-Item .env.example .env
```

Linux：

```bash
cp .env.example .env
chmod 600 .env
mkdir -p data logs
```

然后编辑 `.env`。

### 校园电量接口

```dotenv
GUET_BASE_URL=http://sdcx.guet.edu.cn
GUET_ROOM_NO=y503616
GUET_QUERY_COUNT=10
GUET_ELECTRICITY_THRESHOLD=20
GUET_REQUEST_TIMEOUT=20
GUET_REQUEST_RETRIES=3
GUET_ELECTRICITY_COOKIE=从浏览器或Reqable复制的完整Cookie
```

初始 Cookie 可以为空。没有可用 Cookie或 Cookie 已失效时，程序会自动启动浏览器刷新会话。

### 浏览器验证配置

Windows 使用本机 Chrome：

```dotenv
GUET_COOKIE_FILE=./data/guet-cookies.json
GUET_BROWSER_PROFILE_DIR=./data/guet-chrome-profile
GUET_BROWSER_CHANNEL=chrome
GUET_BROWSER_HEADLESS=false
GUET_VERIFICATION_WAIT_SECONDS=90
GUET_VERIFICATION_RELOAD_DELAY_SECONDS=7
```

Linux 服务器使用 Playwright Chromium：

```dotenv
GUET_COOKIE_FILE=./data/guet-cookies.json
GUET_BROWSER_PROFILE_DIR=./data/guet-chrome-profile
GUET_BROWSER_CHANNEL=
GUET_BROWSER_HEADLESS=true
GUET_VERIFICATION_WAIT_SECONDS=90
GUET_VERIFICATION_RELOAD_DELAY_SECONDS=7
```

Linux 上将 `GUET_BROWSER_CHANNEL` 留空，表示使用 Playwright 安装的 Chromium。

无桌面服务器应使用无头模式。如果网站出现必须人工点击的交互式验证，无头服务器可能无法自动完成，需要重新提供有效 Cookie，或临时使用带图形界面的浏览器环境。

### QQ 邮箱

```dotenv
QQ_SMTP_HOST=smtp.qq.com
QQ_SMTP_PORT=465
QQ_SMTP_TIMEOUT=30
QQ_EMAIL=你的QQ邮箱@qq.com
QQ_AUTH_CODE=QQ邮箱生成的SMTP授权码
ALERT_TO_EMAIL=接收提醒的邮箱地址
```

`QQ_AUTH_CODE` 必须填写 QQ 邮箱生成的 SMTP 授权码，不能填写 QQ 登录密码。

固定邮件主题为：

```text
[宿舍电量监控] 低电量提醒
```

## 测试

下列命令在 Windows 中可将 `python` 替换为相应 Conda 环境的 Python；Linux 中应在项目目录执行，并使用已经测试通过的虚拟环境或 Conda 解释器。

### XML 解析自检

```bash
python electricity_monitor.py --self-test
```

### 测试电量查询

```bash
python electricity_monitor.py --check-only
```

Cookie 有效时直接查询；Cookie 失效时会自动启动浏览器，完成验证后保存新 Cookie。

再次执行相同命令时，通常会复用保存的 Cookie，不再打开浏览器。

### 测试 QQ 邮件

```bash
python electricity_monitor.py --test-email
```

该命令只发送测试邮件，不查询电量，也不会修改低电量提醒状态。

### 强制执行完整检查

```bash
python electricity_monitor.py --force
```

如果当前电量低于阈值，并且本轮低电量状态还没有提醒过，程序会发送邮件。

## 低电量提醒规则

- `remain < GUET_ELECTRICITY_THRESHOLD` 时发送提醒；
- 同一次持续低电量状态只发送一次；
- 电量恢复到阈值及以上时重置提醒状态；
- 下一次再次低于阈值时重新发送；
- 邮件发送失败时不会标记为已提醒，之后仍会继续尝试；
- 中午成功检查后，晚上补偿任务会自动跳过；
- 中午失败时，晚上继续检查。

## Windows 定时任务

安装计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_tasks.ps1
```

默认运行时间：

- 每天 12:00；
- 每天 20:00。

手动触发：

```powershell
Start-ScheduledTask -TaskName "GUET Electricity Monitor"
```

查看任务状态：

```powershell
Get-ScheduledTaskInfo -TaskName "GUET Electricity Monitor"
```

删除计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_tasks.ps1
```

修改 `.env` 后不需要重新安装计划任务。只有脚本路径、Conda 环境名称、任务名称或执行时间发生变化时，才需要重新安装。

## Linux 非管理员用户定时任务

没有 `sudo` 权限时，不要创建 `/etc/systemd/system` 下的系统服务。推荐使用当前用户自己的 `crontab`，任务会以当前用户身份运行。

以下命令全部由部署用户执行，不需要 `sudo`。

### 1. 进入真实项目目录

先在 PyCharm 的服务器终端中进入实际部署目录。不要直接照抄示例路径：

```bash
cd 你的实际项目目录
```

确认当前目录确实是项目根目录：

```bash
pwd -P
ls -la
test -f electricity_monitor.py && echo "项目路径正确"
```

只有看到：

```text
项目路径正确
```

才能继续。

创建日志目录：

```bash
mkdir -p logs
```

### 2. 确认 Python 解释器

激活此前测试成功的虚拟环境。

使用 `venv` 时：

```bash
source .venv/bin/activate
```

使用 Conda 时：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate 你的环境名称
```

记录项目和解释器的真实绝对路径：

```bash
PROJECT_DIR="$(pwd -P)"
PYTHON_EXE="$(python -c 'import os, sys; print(os.path.realpath(sys.executable))')"

printf 'PROJECT_DIR=%s\nPYTHON_EXE=%s\n' \
  "$PROJECT_DIR" "$PYTHON_EXE"
```

验证路径：

```bash
test -f "$PROJECT_DIR/electricity_monitor.py" \
  && echo "程序文件存在"

test -x "$PYTHON_EXE" \
  && echo "Python 解释器可执行"

test -d "$PROJECT_DIR/logs" \
  && echo "日志目录存在"
```

必须确认 `PYTHON_EXE` 就是此前执行 `--check-only` 测试成功的解释器。

### 3. 创建普通用户运行脚本

在项目根目录执行：

```bash
cat > "$PROJECT_DIR/run_monitor.sh" <<EOF
#!/usr/bin/env bash
set -uo pipefail

export HOME="${HOME}"
export PLAYWRIGHT_BROWSERS_PATH="${HOME}/.cache/ms-playwright"

cd "${PROJECT_DIR}"
exec "${PYTHON_EXE}" "${PROJECT_DIR}/electricity_monitor.py"
EOF

chmod 700 "$PROJECT_DIR/run_monitor.sh"
```

查看脚本中写入的路径：

```bash
cat "$PROJECT_DIR/run_monitor.sh"
```

检查语法和权限：

```bash
bash -n "$PROJECT_DIR/run_monitor.sh"
ls -l "$PROJECT_DIR/run_monitor.sh"
```

手动测试：

```bash
"$PROJECT_DIR/run_monitor.sh"
```

如果当天已经成功检查过，程序可能输出：

```text
今天已成功检查过电量，本次补偿任务无需重复请求
```

这是正常现象。

需要强制验证完整流程时，可以临时运行：

```bash
"$PYTHON_EXE" "$PROJECT_DIR/electricity_monitor.py" --force
```

不要在 `run_monitor.sh` 或正式 cron 任务中添加 `--force`。否则每天 12:00 和 20:00 都会强制查询，无法保留“中午主检查、晚上失败补偿”的原有逻辑。

### 4. 检查 crontab 是否可用

```bash
command -v crontab
crontab -l
```

第一次执行 `crontab -l` 时可能显示：

```text
no crontab for 当前用户名
```

这只表示尚未创建个人定时任务，不是错误。

如果显示 `crontab: command not found`，或者服务器明确禁止当前用户使用 cron，需要联系管理员启用 cron。普通用户无法自行安装或启动系统级 cron 服务。

### 5. 生成正确的 cron 配置行

终端中的 `$PROJECT_DIR` 变量不会自动传入 crontab，所以 cron 中必须写入真实绝对路径。

使用下面的命令生成可直接复制的配置：

```bash
printf '0 12 * * * %s/run_monitor.sh >> %s/logs/cron.log 2>&1\n' \
  "$PROJECT_DIR" "$PROJECT_DIR"

printf '0 20 * * * %s/run_monitor.sh >> %s/logs/cron.log 2>&1\n' \
  "$PROJECT_DIR" "$PROJECT_DIR"
```

输出示例可能是：

```cron
0 12 * * * /home/zkl/tmp/pycharm_project/electricity_monitor/run_monitor.sh >> /home/zkl/tmp/pycharm_project/electricity_monitor/logs/cron.log 2>&1
0 20 * * * /home/zkl/tmp/pycharm_project/electricity_monitor/run_monitor.sh >> /home/zkl/tmp/pycharm_project/electricity_monitor/logs/cron.log 2>&1
```

示例仅用于说明格式。应以你终端实际输出的路径为准。

### 6. 编辑当前用户的定时任务

执行：

```bash
crontab -e
```

第一次运行时，系统可能要求选择编辑器，可以选择 `nano`。

在文件末尾添加：

```cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO=""

0 12 * * * 这里粘贴实际项目路径/run_monitor.sh >> 这里粘贴实际项目路径/logs/cron.log 2>&1
0 20 * * * 这里粘贴实际项目路径/run_monitor.sh >> 这里粘贴实际项目路径/logs/cron.log 2>&1
```

必须将两条任务替换为上一步 `printf` 命令生成的完整内容，不要保留占位文字，也不要在 crontab 中写 `$PROJECT_DIR`。

时间安排：

- 每天 12:00：主检查；
- 每天 20:00：失败补偿检查。

保存并退出后，crontab 会自动安装。

### 7. 检查是否写入成功

```bash
crontab -l
```

检查 crontab 中是否仍有占位符或变量：

```bash
crontab -l | grep -E 'PROJECT_DIR|path/to|这里粘贴' \
  && echo "发现错误路径，请重新执行 crontab -e 修改" \
  || echo "未发现路径占位符"
```

再次检查目标文件：

```bash
ls -l "$PROJECT_DIR/run_monitor.sh"
ls -ld "$PROJECT_DIR/logs"
```

### 8. 立即验证正式命令

执行和 cron 相同的命令：

```bash
"$PROJECT_DIR/run_monitor.sh" \
  >> "$PROJECT_DIR/logs/cron.log" 2>&1
```

查看日志：

```bash
tail -n 100 "$PROJECT_DIR/logs/cron.log"
tail -n 100 "$PROJECT_DIR/logs/electricity-monitor.log"
```

如果日志文件不存在，先检查：

```bash
echo "$PROJECT_DIR"
test -d "$PROJECT_DIR/logs" && echo "日志目录存在"
test -x "$PROJECT_DIR/run_monitor.sh" && echo "运行脚本可执行"
```

不要使用旧终端会话中保存的错误路径。重新进入项目目录并执行：

```bash
PROJECT_DIR="$(pwd -P)"
```

### 9. 临时测试 cron 是否触发

先生成测试行：

```bash
printf '* * * * * %s/run_monitor.sh >> %s/logs/cron-test.log 2>&1\n' \
  "$PROJECT_DIR" "$PROJECT_DIR"
```

执行：

```bash
crontab -e
```

将刚才输出的完整一行临时添加到 crontab。等待跨过下一个整分钟后查看：

```bash
tail -n 100 "$PROJECT_DIR/logs/cron-test.log"
```

如果 `cron-test.log` 仍不存在，先确认查看日志时使用的 `$PROJECT_DIR` 与 crontab 中的绝对路径完全一致：

```bash
echo "$PROJECT_DIR"
crontab -l
```

还可以用最简单的心跳任务排除 Python 和 Playwright 的影响：

```bash
printf '* * * * * /usr/bin/date >> %s/logs/cron-heartbeat.log 2>&1\n' \
  "$PROJECT_DIR"
```

将输出内容临时加入 `crontab -e`，等待一至两分钟后查看：

```bash
cat "$PROJECT_DIR/logs/cron-heartbeat.log"
```

确认 cron 能正常触发后，必须删除每分钟测试任务，只保留每天 12:00 和 20:00 两条正式任务。

### 10. 时区说明

先查看服务器时间：

```bash
date
timedatectl 2>/dev/null || true
```

如果服务器时间已经是北京时间，直接使用 `12` 和 `20` 即可。

部分 cron 实现支持在 crontab 顶部添加：

```cron
CRON_TZ=Asia/Shanghai
```

但并非所有服务器环境都保证支持。最稳妥的方式是根据 `date` 显示的服务器时区，换算成对应的 cron 小时。

### 11. 常用管理命令

查看当前用户任务：

```bash
crontab -l
```

编辑任务：

```bash
crontab -e
```

备份任务：

```bash
crontab -l > ~/electricity-monitor-crontab.backup
```

恢复任务：

```bash
crontab ~/electricity-monitor-crontab.backup
```

删除当前用户的全部定时任务：

```bash
crontab -r
```

`crontab -r` 会删除当前用户的所有 cron 任务，不只是本项目。执行前应先运行 `crontab -l` 并做好备份。

修改 `.env`、Python 代码或 `run_monitor.sh` 后，不需要重新创建定时任务。下一次触发时会读取最新文件。

## 日志与状态

Windows 查看最近日志：

```powershell
Get-Content .\logs\electricity-monitor.log -Tail 50
```

Linux 查看最近日志：

```bash
tail -n 50 logs/electricity-monitor.log
```

删除损坏的状态文件。

Windows：

```powershell
Remove-Item .\data\state.json -Force -ErrorAction SilentlyContinue
```

Linux：

```bash
rm -f ./data/state.json
```

## 邮件分类建议

在收件邮箱中创建过滤规则：

```text
主题等于：[宿舍电量监控] 低电量提醒
```

然后将匹配邮件自动移动到单独文件夹或添加标签。

## 安全说明

以下内容不得提交到 GitHub：

- `.env`；
- 校园接口 Cookie；
- QQ 邮箱 SMTP 授权码；
- `data/guet-cookies.json`；
- `data/guet-chrome-profile/`；
- Playwright 浏览器缓存；
- 运行日志和状态文件。

建议 `.gitignore` 至少包含：

```gitignore
.env
.venv/
data/
logs/
__pycache__/
*.pyc
.idea/
```
