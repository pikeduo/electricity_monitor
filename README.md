# 宿舍电量监控

该目录已经包含：

- `environment.yml`：Conda 环境配置；
- `.env`：本机环境变量，已写入你提供的 Cookie；
- `.env.example`：不含真实凭据的模板；
- `electricity_monitor.py`：查询、解析、状态管理和 Gmail 提醒；
- `install_tasks.ps1`：创建每天 12:00 和 20:00 的 Windows 定时任务；
- `uninstall_tasks.ps1`：删除定时任务；
- `.gitignore`：防止 `.env`、日志和状态文件被提交。

## 1. 创建 Conda 环境

在项目目录中执行：

```powershell
conda env create -f environment.yml
conda activate electricity-monitor
```

环境已经存在时更新：

```powershell
conda env update -n electricity-monitor -f environment.yml --prune
```

## 2. 先运行解析自检

```powershell
python .\electricity_monitor.py --self-test
```

预期：

```text
自检通过：成功解析样例 XML，剩余电量为 99.90
```

## 3. 测试真实接口

```powershell
python .\electricity_monitor.py --check-only
```

成功后会输出房间号、记录时间和剩余电量。

如果提示“真人检测页面”，说明 `.env` 中的 `GUET_ELECTRICITY_COOKIE` 已过期。请从 Reqable 成功请求中复制完整 Cookie，替换 `.env` 中的值。

## 4. 配置 Gmail 提醒

打开 `.env`，填写：

```dotenv
GMAIL_USERNAME=你的Gmail地址
GMAIL_APP_PASSWORD=Google生成的16位应用专用密码
ALERT_TO_EMAIL=接收提醒的Gmail地址
```

不要填写普通 Gmail 登录密码。

脚本行为：

- 剩余电量低于 `GUET_ELECTRICITY_THRESHOLD` 时发送邮件；
- 低电量状态持续期间只发送一次；
- 电量恢复到阈值以上后重置提醒；
- 中午成功检查后，晚上任务自动跳过；
- 中午失败时，晚上继续尝试；
- 网络失败不会写入“当天已成功”状态。

手动执行完整监控：

```powershell
python .\electricity_monitor.py --force
```

## 5. 安装 Windows 定时任务

建议在普通 PowerShell 或 Anaconda Prompt 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_tasks.ps1
```

计划任务每天运行：

- 12:00；
- 20:00。

任务失败时，每 15 分钟重试一次，最多重试 2 次；电脑错过执行时间后会尽快补跑。

## 6. 删除计划任务

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_tasks.ps1
```

## 安全说明

`.env` 中含有 Cookie 和未来填写的 Gmail 应用密码，已经被 `.gitignore` 排除，不要上传到 GitHub、网盘或公开聊天。

你之前已在聊天中公开过当前 Cookie。建议确认脚本运行成功后，重新通过浏览器/Reqable 获取新的 Cookie 并更新 `.env`。
