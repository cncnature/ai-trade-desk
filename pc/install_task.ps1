# Server PC: register weekday tasks (GitHub's own schedule can lag by hours, so the PC sends the signals)
#   06:30             AITradeDesk_KickDividends   -> dividends workflow
#   07:30/07:50/08:10 AITradeDesk_FlowExport      flow.db -> data/flow -> morning analysis (pushes once, as soon as new flow arrives;
#                                                 at 08:10 sends a signal with yesterday's flow if none arrived)
#   07:45/08:00/08:20 AITradeDesk_V9MorningExport morning_pdf.db -> docs/data/v9_morning.json (only when changed)
#   16:05             AITradeDesk_KickClose       -> close-quotes workflow
#   20:15             AITradeDesk_KickEval        -> evening-eval workflow
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File pc\install_task.ps1
$repo     = Split-Path -Parent $PSScriptRoot
$python   = (& python -c "import sys; print(sys.executable)").Trim()   # real exe, not the WindowsApps alias
$days     = "Monday","Tuesday","Wednesday","Thursday","Friday"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$tasks = @(
  @("AITradeDesk_KickDividends",  "pc\kick.py dividends",      @("06:30")),
  @("AITradeDesk_FlowExport",     "pc\export_flow.py",         @("07:30","07:50","08:10")),
  @("AITradeDesk_V9MorningExport","pc\export_v9_morning.py",   @("07:45","08:00","08:20")),
  @("AITradeDesk_KickClose",      "pc\kick.py close",          @("16:05")),
  @("AITradeDesk_KickEval",       "pc\kick.py eval",           @("20:15"))
)
foreach ($t in $tasks) {
  $action   = New-ScheduledTaskAction -Execute $python -Argument $t[1] -WorkingDirectory $repo
  $triggers = $t[2] | ForEach-Object { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $_ }
  Register-ScheduledTask -TaskName $t[0] -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null
  Write-Host "OK: $($t[0]) (weekdays $($t[2] -join ', '))"
}
Write-Host "python=$python  repo=$repo"
