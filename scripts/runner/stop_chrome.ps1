# Quit only the bot Chrome (twitter-bot-chrome profile), and wait until it exits
# so the X login cookies are written to that profile. The personal Chrome is left alone.
$ErrorActionPreference = "Continue"

$ChromePort = 9222
$UserDataMarker = "twitter-bot-chrome"
$QuitScript = Join-Path $PSScriptRoot "chrome_quit.py"

function Get-BotChromeMain {
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*$UserDataMarker*" -and
            $_.CommandLine -notlike "*--type=*"
        }
}

$running = @(Get-BotChromeMain)
if (-not $running) {
    Write-Host "Bot Chrome is not running"
    exit 0
}

$python = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $python = $cmd.Source
        break
    }
}
if ($python) {
    & $python $QuitScript --port $ChromePort
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "CDP Browser.close failed; falling back to closing the window."
    }
} else {
    Write-Warning "Python not found; closing the bot Chrome window instead of CDP."
}

$deadline = (Get-Date).AddSeconds(8)
while ((Get-Date) -lt $deadline) {
    if (-not @(Get-BotChromeMain)) {
        Write-Host "Bot Chrome exited. X profile saved."
        exit 0
    }
    Start-Sleep -Seconds 1
}

foreach ($procInfo in @(Get-BotChromeMain)) {
    $proc = Get-Process -Id $procInfo.ProcessId -ErrorAction SilentlyContinue
    if ($proc -and $proc.MainWindowHandle -ne 0) {
        [void]$proc.CloseMainWindow()
    }
}

$deadline = (Get-Date).AddSeconds(12)
while ((Get-Date) -lt $deadline) {
    if (-not @(Get-BotChromeMain)) {
        Write-Host "Bot Chrome exited. X profile saved."
        exit 0
    }
    Start-Sleep -Seconds 1
}

Write-Warning "Bot Chrome did not exit after a graceful close. Leaving it running so the X login is not killed mid-write."
exit 1
