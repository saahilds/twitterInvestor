# Register Windows tasks that keep the bot's dependencies alive while you are away:
#   - WslKeepAlive: keeps the Ubuntu VM running (WSL shuts idle distros down, which
#     would stop the twitter-bot systemd service).
#   - ChromeWatchdog: every 5 minutes, (re)starts Chrome CDP + the CDP relay if either died.
#     start_chrome.ps1 itself no-ops outside Mon-Fri 9:30-16:00 ET unless a backfill lock is held.
# Both run at logon, so Windows must log in automatically after a reboot.
# Run in PowerShell (no admin needed):
#   powershell -ExecutionPolicy Bypass -File scripts\runner\install_watchdog_tasks.ps1
param(
    [string]$WslDistro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$TaskPrefix = "TwitterInvestor"
$chromeScript = (Resolve-Path (Join-Path $PSScriptRoot "start_chrome.ps1")).Path

function New-RepeatingTrigger {
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
    return $trigger
}

$keepAliveAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"wsl.exe -d $WslDistro --exec sleep infinity`""
$keepAliveSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "$TaskPrefix-WslKeepAlive" -Action $keepAliveAction `
    -Trigger @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME), (New-RepeatingTrigger)) `
    -Settings $keepAliveSettings -Force | Out-Null
Write-Host "Registered $TaskPrefix-WslKeepAlive (at logon + re-checked every 5 min)"

$chromeAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$chromeScript`""
$chromeSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "$TaskPrefix-ChromeWatchdog" -Action $chromeAction `
    -Trigger @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME), (New-RepeatingTrigger)) `
    -Settings $chromeSettings -Force | Out-Null
Write-Host "Registered $TaskPrefix-ChromeWatchdog (at logon + every 5 min)"

Start-ScheduledTask -TaskName "$TaskPrefix-WslKeepAlive"
Start-ScheduledTask -TaskName "$TaskPrefix-ChromeWatchdog"
Write-Host "Started both tasks."
