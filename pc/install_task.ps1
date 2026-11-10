# Server PC: register weekday upload tasks
#   07:30          AITradeDesk_FlowExport      flow.db -> data/flow   (starts the morning analysis on GitHub)
#   07:45/08:00/08:20 AITradeDesk_V9MorningExport morning_pdf.db -> docs/data/v9_morning.json (reference only)
#     (re-uploads only when the list changed, so a late ignition-radar update is still picked up)
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File pc\install_task.ps1
$repo     = Split-Path -Parent $PSScriptRoot
$python   = (& python -c "import sys; print(sys.executable)").Trim()   # real exe, not the WindowsApps alias
$days     = "Monday","Tuesday","Wednesday","Thursday","Friday"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
foreach ($t in @(@("AITradeDesk_FlowExport","pc\export_flow.py",@("07:30")),
                 @("AITradeDesk_V9MorningExport","pc\export_v9_morning.py",@("07:45","08:00","08:20")))) {
  $action   = New-ScheduledTaskAction -Execute $python -Argument $t[1] -WorkingDirectory $repo
  $triggers = $t[2] | ForEach-Object { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $_ }
  Register-ScheduledTask -TaskName $t[0] -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null
  Write-Host "OK: $($t[0]) (weekdays $($t[2] -join ', '))"
}
Write-Host "python=$python  repo=$repo"
