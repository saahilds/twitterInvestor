# Register Windows Task Scheduler jobs that invoke WSL scripts (WSL VM may be idle).
# Run in PowerShell: powershell -ExecutionPolicy Bypass -File scripts/runner/install_windows_tasks.ps1
param(
    [string]$WslDistro = "Ubuntu",
    [string]$RepoPath = "/home/$env:USERNAME/twitterInvestor"
)

$ErrorActionPreference = "Stop"
$TaskPrefix = "TwitterInvestor"

function Register-WslTask {
    param(
        [string]$Name,
        [string]$Time,
        [string]$Days,
        [string]$WslCommand
    )

    $action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $WslDistro -- bash -lc '$WslCommand'"
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Days -At $Time
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName "$TaskPrefix-$Name" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Registered $TaskPrefix-$Name at $Time on $Days"
}

$repo = $RepoPath

# Chrome CDP at logon
$chromeScript = (Resolve-Path (Join-Path $PSScriptRoot "start_chrome.ps1")).Path
$chromeAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$chromeScript`""
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "$TaskPrefix-ChromeCDP" -Action $chromeAction -Trigger $logonTrigger -Force | Out-Null
Write-Host "Registered $TaskPrefix-ChromeCDP at logon"

# Mon-Fri ET — Task Scheduler uses local time; set Windows timezone to Eastern or adjust times.
Register-WslTask -Name "UpdateIfBehind" -Time "06:00" -Days Monday,Tuesday,Wednesday,Thursday,Friday -WslCommand "cd $repo && ./scripts/runner/update_if_behind.sh"
Register-WslTask -Name "MorningBackfill" -Time "07:00" -Days Monday,Tuesday,Wednesday,Thursday,Friday -WslCommand "cd $repo && ./scripts/startup_backfill.sh"
Register-WslTask -Name "EveningPause" -Time "20:00" -Days Monday,Tuesday,Wednesday,Thursday,Friday -WslCommand "cd $repo && ./scripts/evening_pause.sh"

Write-Host ""
Write-Host "Done. Ensure Windows timezone is America/New_York (or adjust task times)."
Write-Host "Edit RepoPath in this script if your WSL clone is not at: $repo"
