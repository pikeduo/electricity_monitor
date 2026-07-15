param(
    [string]$TaskName = "GUET Electricity Monitor"
)

$ErrorActionPreference = "Stop"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $Task) {
    Write-Host "Scheduled task was not found: $TaskName" -ForegroundColor Yellow
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

Write-Host "Scheduled task removed successfully." -ForegroundColor Green
Write-Host "Task name: $TaskName"