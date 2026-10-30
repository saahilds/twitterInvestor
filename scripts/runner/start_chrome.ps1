# Launch Chrome with remote debugging for Playwright CDP attach from WSL.
# Run once manually, or register via install_windows_tasks.ps1 at logon.
# The scheduled watchdog calls this with no arguments, so outside market hours it
# leaves Chrome stopped. Pass -Force to open the window yourself (for example to log in).
param(
    [switch]$Force
)

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

$RelayScript = Join-Path $PSScriptRoot "cdp_relay.py"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvFile = Join-Path $RepoRoot ".env"
$RelayPort = 9223
$ChromePort = 9222

function Test-RegularMarketHours {
    # Same window as app/risk/market_hours.py: Mon-Fri 9:30-16:00 America/New_York.
    $eastern = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $now = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $eastern)
    if ($now.DayOfWeek -eq [DayOfWeek]::Saturday -or $now.DayOfWeek -eq [DayOfWeek]::Sunday) {
        return $false
    }
    $minutes = $now.Hour * 60 + $now.Minute
    return ($minutes -ge (9 * 60 + 30)) -and ($minutes -lt (16 * 60))
}

function Get-BackfillLockPath {
    # Same file as app/ingestion/backfill_lock.py: beside LOG_FILE, default logs\bot.log.
    $logFile = "logs\bot.log"
    if (Test-Path -LiteralPath $EnvFile) {
        $match = Select-String -Path $EnvFile -Pattern '^(?i)LOG_FILE=(.+)$' | Select-Object -Last 1
        if ($match) {
            $value = $match.Matches[0].Groups[1].Value.Trim().Trim('"')
            if ($value) { $logFile = $value }
        }
    }
    if (-not [System.IO.Path]::IsPathRooted($logFile)) {
        $logFile = Join-Path $RepoRoot $logFile
    }
    return Join-Path (Split-Path -Parent $logFile) "backfill.lock"
}

function Test-BackfillLockFresh {
    # 7 AM backfill holds this lock so Chrome can load X before the open.
    $lock = Get-BackfillLockPath
    if (-not (Test-Path -LiteralPath $lock)) { return $false }
    $age = (Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime
    return $age.TotalMinutes -lt 30
}

# The 5-minute watchdog calls this script all night. Starting Chrome then
# undoes the after-hours quit and keeps the fan running.
if (-not $Force -and -not (Test-RegularMarketHours) -and -not (Test-BackfillLockFresh)) {
    Write-Host "Outside market hours; leaving bot Chrome stopped. Re-run with -Force to open it."
    exit 0
}

function Get-WslReachableHostIp {
    # WSL often cannot reach the Hyper-V vEthernet gateway (172.x) due to Windows
    # Firewall, but can reach the normal LAN/Wi-Fi IPv4 address.
    $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.PrefixOrigin -ne "WellKnown"
        } |
        Sort-Object {
            # Prefer private LAN over Tailscale CGNAT / other
            if ($_.IPAddress -like "192.168.*") { 0 }
            elseif ($_.IPAddress -like "10.*") { 1 }
            elseif ($_.IPAddress -like "172.1[6-9].*" -or $_.IPAddress -like "172.2[0-9].*" -or $_.IPAddress -like "172.3[0-1].*") { 2 }
            else { 3 }
        } |
        Select-Object -ExpandProperty IPAddress -Unique

    foreach ($ip in $candidates) {
        # Skip the WSL virtual switch host address - firewall often blocks it.
        $ifAlias = (Get-NetIPAddress -IPAddress $ip -ErrorAction SilentlyContinue).InterfaceAlias
        if ($ifAlias -like "*WSL*") { continue }
        return $ip
    }
    return "127.0.0.1"
}

function Set-EnvCdpUrl([string]$Path, [string]$Url) {
    if (-not (Test-Path $Path)) {
        Write-Warning ".env not found at $Path - set PLAYWRIGHT_CDP_URL=$Url manually"
        return
    }
    $text = Get-Content -Raw $Path
    $original = $text
    $line = "PLAYWRIGHT_CDP_URL=$Url"
    if ($text -match "(?m)^PLAYWRIGHT_CDP_URL=.*$") {
        $text = [regex]::Replace($text, "(?m)^PLAYWRIGHT_CDP_URL=.*$", $line, 1)
    } else {
        if (-not $text.EndsWith("`n")) { $text += "`n" }
        $text += $line + "`n"
    }
    if ($text -ceq $original) {
        return
    }
    # Windows PowerShell 5 Set-Content writes UTF-16, which breaks python-dotenv.
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $text, $utf8)
    Write-Host "Updated .env PLAYWRIGHT_CDP_URL=$Url"
}

# Chrome binds CDP to 127.0.0.1 only. Relay exposes 0.0.0.0:9223 for WSL2.
$relayUp = Get-NetTCPConnection -LocalPort $RelayPort -State Listen -ErrorAction SilentlyContinue
if (-not $relayUp) {
    $python = $null
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $python = $cmd.Source
            break
        }
    }
    if (-not $python) {
        Write-Warning "Python not found on PATH; WSL cannot reach Chrome CDP without a relay."
    } else {
        Write-Host "Starting CDP relay on 0.0.0.0:$RelayPort -> 127.0.0.1:$ChromePort"
        Start-Process -FilePath $python -ArgumentList @("`"$RelayScript`"", "--listen-port", "$RelayPort", "--target-port", "$ChromePort") -WindowStyle Hidden
        Start-Sleep -Seconds 1
    }
} else {
    Write-Host "CDP relay already listening on port $RelayPort"
}

$chromeArgs = @(
    "--remote-debugging-port=$ChromePort",
    "--user-data-dir=$UserData",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-minimized",
    "--disable-sync",
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-component-update",
    "--disable-background-networking",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--mute-audio",
    "--autoplay-policy=user-gesture-required",
    "--disk-cache-size=104857600",
    "--media-cache-size=1",
    "--disable-features=OptimizationGuideModelDownloading,OptimizationHints,MediaRouter,Translate,InterestFeedContentSuggestions,CalculateNativeWinOcclusion,HeavyAdPrivacyMitigations,BackForwardCache,GlobalMediaControls",
    "about:blank"
)

$cdpUp = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$ChromePort/json/version" -TimeoutSec 2
    if ($resp.StatusCode -eq 200) { $cdpUp = $true }
} catch {
    $cdpUp = $false
}

if ($cdpUp) {
    Write-Host "Chrome CDP already up on 127.0.0.1:$ChromePort"
} else {
    Write-Host "Starting Chrome CDP on port $ChromePort (user-data: $UserData)"
    Start-Process -FilePath $Chrome -ArgumentList $chromeArgs -WindowStyle Minimized
}

$hostIp = Get-WslReachableHostIp
$cdpUrl = "http://${hostIp}:$RelayPort"
Set-EnvCdpUrl -Path $EnvFile -Url $cdpUrl
Write-Host "From WSL verify: curl -s $cdpUrl/json/version"
Write-Host "Then log into x.com once in the Chrome window if needed, and restart the bot."
