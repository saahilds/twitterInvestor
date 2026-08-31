# Launch Chrome with remote debugging for Playwright CDP attach from WSL.
# Run once manually, or register via install_windows_tasks.ps1 at logon.
$ErrorActionPreference = "Stop"

$ChromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LOCALAPPDATA}\Google\Chrome\Application\chrome.exe"
)

$Chrome = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Chrome) {
    Write-Error "Google Chrome not found. Install Chrome on Windows first."
}

$UserData = Join-Path $env:LOCALAPPDATA "twitter-bot-chrome"
New-Item -ItemType Directory -Force -Path $UserData | Out-Null

$args = @(
    "--remote-debugging-port=9222",
    "--user-data-dir=$UserData",
    "--no-first-run",
    "--no-default-browser-check"
)

Write-Host "Starting Chrome CDP on port 9222 (user-data: $UserData)"
Start-Process -FilePath $Chrome -ArgumentList $args
