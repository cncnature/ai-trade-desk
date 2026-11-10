# Server PC: register weekday 07:30 flow upload task
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File pc\install_task.ps1
$repo    = Split-Path -Parent $PSScriptRoot
$python  = (& python -c "import sys; print(sys.executable)").Trim()   # real exe, not the WindowsApps alias
$action  = New-ScheduledTaskAction -Execute $python -Argument "pc\export_flow.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 07:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName "AITradeDesk_FlowExport" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "OK: AITradeDesk_FlowExport (weekdays 07:30)  python=$python  repo=$repo"
