# Register Windows Task Scheduler job: daily 9 AM fetch + merge + pipeline
# Run once in elevated PowerShell from meridianai-stockpulse-backend folder:
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\scripts\register_daily_task.ps1

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
$BatPath = Join-Path $BackendRoot "scripts\run_daily_pipeline.bat"
$TaskName = "StockPulse-DailyPipeline"

if (-not (Test-Path $BatPath)) {
    throw "Missing $BatPath"
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $BackendRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At "09:00"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "StockPulse: daily blob fetch, merge, combine at 9 AM (refill manual)" `
    -Force

Write-Host "Registered scheduled task: $TaskName (09:00 daily)"
Write-Host "Backend also runs in-app scheduler via DAILY_PIPELINE_SCHEDULER=1 on uvicorn startup."
