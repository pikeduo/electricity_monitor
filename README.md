# 宿舍电量监控

这是一个基于 Python 的桂电宿舍电量自动监控项目。程序会定时查询校园电量接口，解析最新剩余电量，并在电量低于设定阈值时通过 QQ 邮箱发送提醒。

邮件主题固定为：

```text
[宿舍电量监控] 低电量提醒
```

因此可以在邮箱中按主题建立过滤规则，将所有低电量提醒自动归类。

## 功能

- 查询宿舍最新剩余电量；
- 解析 ASP.NET DataTable XML；
- 优先使用 `requests + Cookie` 查询；
- Cookie 失效时自动启动本机 Google Chrome 完成人机验证；
- 保存刷新后的 Cookie，后续查询不再重复打开浏览器；
- 通过 QQ 邮箱 SMTP 发送低电量提醒；
- 低电量期间只提醒一次，充值后自动重置；
- 支持每天 12:00 和 20:00 的 Windows 定时任务；
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
        启动本机 Google Chrome
                 │
                 ▼
        正常完成网页验证
                 │
                 ▼
        保存新的 Cookie
                 │
                 ▼
        获取并解析剩余电量
```

浏览器使用程序专用的用户目录，不会占用日常 Chrome 配置。

## 项目文件

```text
electricity-monitor/
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

- `.env`：本机私密配置，不应提交到 Git；
- `.env.example`：配置模板，不包含真实凭据；
- `data/guet-cookies.json`：浏览器刷新后保存的 Cookie；
- `data/guet-chrome-profile/`：Playwright 专用 Chrome 用户目录；
- `data/state.json`：上次检查和提醒状态；
- `logs/electricity-monitor.log`：运行日志。

## 环境要求

- Windows 10 或 Windows 11；
- Python 3.11；
- 已安装 Google Chrome；
- 可访问桂电校园电量查询接口；
- 已开启 SMTP 服务的 QQ 邮箱。

## 安装依赖

### 使用 Conda

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

程序默认调用电脑中已经安装的 Google Chrome，通常不需要执行：

```powershell
playwright install chromium
```

## 配置环境变量

复制模板：

```powershell
Copy-Item .env.example .env
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

初始 Cookie 可以为空。没有可用 Cookie，或 Cookie 已失效时，程序会自动启动 Chrome。

### Chrome 验证配置

```dotenv
GUET_COOKIE_FILE=./data/guet-cookies.json
GUET_BROWSER_PROFILE_DIR=./data/guet-chrome-profile
GUET_BROWSER_CHANNEL=chrome
GUET_BROWSER_HEADLESS=true
GUET_VERIFICATION_WAIT_SECONDS=90
GUET_VERIFICATION_RELOAD_DELAY_SECONDS=7
```

建议保持：

```dotenv
GUET_BROWSER_HEADLESS=false
```

有界面模式更适合网站真人检测。如果出现需要人工点击的验证，可以直接在弹出的 Chrome 窗口中完成。

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

### XML 解析自检

```powershell
python .\electricity_monitor.py --self-test
```

### 测试电量查询

```powershell
python .\electricity_monitor.py --check-only
```

Cookie 有效时直接查询；Cookie 失效时会自动打开 Chrome，完成验证后保存新 Cookie。

再次执行相同命令时，通常会直接复用保存的 Cookie，不再打开浏览器。

### 测试 QQ 邮件

```powershell
python .\electricity_monitor.py --test-email
```

该命令只发送测试邮件，不查询电量，也不会修改低电量提醒状态。

### 强制执行完整检查

```powershell
python .\electricity_monitor.py --force
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

## 日志与状态

查看最近日志：

```powershell
Get-Content .\logs\electricity-monitor.log -Tail 50
```

删除损坏的状态文件：

```powershell
Remove-Item .\data\state.json -Force -ErrorAction SilentlyContinue
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
- 运行日志和状态文件。

建议 `.gitignore` 至少包含：

```gitignore
.env
data/
logs/
__pycache__/
*.pyc
.idea/
```
