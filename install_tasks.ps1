param(
    [string]$EnvironmentName = "electricity-monitor",
    [string]$TaskName = "GUET Electricity Monitor"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ProjectDir "electricity_monitor.py"

if (-not (Test-Path $ScriptPath)) {
    throw "Script not found: $ScriptPath"
}

$CondaCommand = Get-Command conda -ErrorAction SilentlyContinue
if (-not $CondaCommand) {
    throw "Conda was not found. Run this script from Anaconda Prompt or add Conda to PATH."
}

# Resolve the exact Python executable inside the target Conda environment.
$PythonOutput = & conda run --no-capture-output -n $EnvironmentName `
    python -c "import sys; print(sys.executable)"

if ($LASTEXITCODE -ne 0) {
    throw "Unable to access Conda environment: $EnvironmentName"
}

$PythonExe = ($PythonOutput | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim()

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $ProjectDir

$TriggerNoon = New-ScheduledTaskTrigger -Daily -At 12:00
$TriggerEvening = New-ScheduledTaskTrigger -Daily -At 20:00

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($TriggerNoon, $TriggerEvening) `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Check GUET dorm electricity at 12:00 and 20:00 every day." `
    -Force | Out-Null

Write-Host ""
Write-Host "Scheduled task created successfully." -ForegroundColor Green
Write-Host "Task name: $TaskName"
Write-Host "Environment: $EnvironmentName"
Write-Host "Python: $PythonExe"
Write-Host "Script: $ScriptPath"
Write-Host "Schedule: 12:00 and 20:00 every day"
Write-Host ""
Write-Host "Run the task manually:"
Write-Host "Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host ""
Write-Host "Check task status:"
Write-Host "Get-ScheduledTask -TaskName `"$TaskName`""