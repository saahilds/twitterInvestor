# Home runner (Windows 10 + WSL2 + uv)

Run the bot 24/7 on a home Windows PC while you develop on a Mac. Code updates via `git pull` on `main`; DB and sessions stay on the home machine.

**Not in git:** `trading_bot.db`, `.env`, X profile, Robinhood tokens. Use one-time pack/unpack.

For Hetzner/VPS deployment see [VPS.md](VPS.md).

## Architecture

- **WSL2 Ubuntu** runs the bot (`uvicorn` + worker) via `scripts/runner/start.sh`.
- **Chrome on Windows** runs with remote debugging; Playwright in WSL attaches via `PLAYWRIGHT_CDP_URL` (Win10 has no WSLg).
- **Windows Task Scheduler** wakes WSL for daily pull, morning backfill, evening pause (cron inside WSL is not enough when the VM sleeps).
- **Tailscale** on Windows for vacation dashboard access (pause, RH reauth).

## One-time: Mac (dev machine)

1. Pause/stop the bot so SQLite is idle:
   ```bash
   curl -X POST http://127.0.0.1:8000/pause   # or stop uvicorn
   ```
2. Pack state:
   ```bash
   ./scripts/pack_runner_state.sh
   ```
   Creates e.g. `~/Desktop/twitterInvestor-runner-state-YYYYMMDD.tar.gz`.
3. Push code to `main` on GitHub.
4. Transfer the tarball **privately** (USB recommended; private Drive OK — delete after unpack).

Do not run live trading on the Mac after the home runner is live.

## One-time: Windows 10 (runner PC)

### Power & network

- Settings → Power: disable sleep/hibernate.
- Prefer ethernet; keep Wi‑Fi stable.

### WSL2 + repo

```powershell
wsl --install -d Ubuntu
```

In WSL:

```bash
sudo apt update && sudo apt install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <your-repo-url> ~/twitterInvestor
cd ~/twitterInvestor
git checkout main
```

### Unpack state

Copy the tarball into WSL (e.g. `\\wsl$\Ubuntu\home\you\twitterInvestor\` or USB mount), then:

```bash
./scripts/unpack_runner_state.sh /path/to/twitterInvestor-runner-state-YYYYMMDD.tar.gz
./scripts/runner/first_boot.sh
```

`unpack_runner_state.sh` places files under `data/`, syncs RH tokens to `~/.tokens`, and rewrites `.env` for runner paths + CDP URL.

### Chrome CDP (X ingest)

In **PowerShell** (Windows):

```powershell
cd C:\path\to\twitterInvestor   # or mount WSL path
powershell -ExecutionPolicy Bypass -File scripts\runner\start_chrome.ps1
```

Log into X once in that Chrome window if the Mac session did not transfer.

Register Chrome at logon + scheduled WSL jobs:

```powershell
# Edit RepoPath inside the script if your clone is not /home/<user>/twitterInvestor
powershell -ExecutionPolicy Bypass -File scripts\runner\install_windows_tasks.ps1
```

Verify CDP from WSL:

```bash
HOST=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
curl -s "http://${HOST}:9222/json/version"
```

### Start the bot

```bash
cd ~/twitterInvestor
./scripts/runner/start.sh
./scripts/runner/status.sh
```

Dashboard on the PC: `http://127.0.0.1:8000/dashboard`

### Tailscale (vacation control)

1. Install Tailscale on Windows and your phone/Mac.
2. Forward WSL port 8000 to Windows (run in **elevated PowerShell**; WSL IP changes after reboot — re-run if dashboard stops working remotely):

```powershell
$wslIp = (wsl hostname -I).Trim().Split(" ")[0]
netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0
netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=$wslIp
```

3. From vacation: `http://<windows-tailscale-ip>:8000/dashboard` — Pause, Refresh RH auth, etc.

Do not expose port 8000 on the public internet without auth.

### Robinhood weekly reauth

- Dashboard → **Refresh RH auth** → approve on phone (~7 day device approval).
- Backup: `./scripts/rh_reauth.sh` from WSL.

If RH login fails after unpack:

```bash
uv run python -m app.cli.rh_login
```

## Daily operations (automatic)

| Time (ET, Mon–Fri) | Script |
|--------------------|--------|
| 6:00 AM | `scripts/runner/update_if_behind.sh` — pull `origin/main` only if behind |
| 7:00 AM | `scripts/startup_backfill.sh` — backfill + resume |
| 8:00 PM | `scripts/evening_pause.sh` — pause + digest |

Manual:

```bash
./scripts/runner/update_if_behind.sh   # stop → pull → restart → resume
./scripts/runner/stop.sh
./scripts/runner/start.sh
```

## Smoke test after migration

```bash
HOST=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
curl -s "http://${HOST}:9222/json/version" | head
./scripts/runner/status.sh
curl -s http://127.0.0.1:8000/health
sqlite3 data/trading_bot.db 'select count(*) from tweets'
```

Wait one poll cycle; confirm new tweets or stable ingest in logs: `tail -f data/logs/bot.log`

## File layout on runner

| Path | Purpose |
|------|---------|
| `data/trading_bot.db` | Trade history |
| `data/x-profile/` | Playwright profile fallback |
| `data/rh-tokens/` | RH pickle backup; live copy in `~/.tokens` |
| `data/logs/` | Bot + update logs |
| `.env` | Secrets (see `.env.runner.example`) |

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `tweet_fetch_failed` / CDP | Run `start_chrome.ps1`; verify `curl` to `:9222` from WSL |
| No tweets | Worker paused → `curl -X POST http://127.0.0.1:8000/resume` |
| RH not logged in | Dashboard Refresh RH auth or `uv run python -m app.cli.rh_login` |
| Scheduled tasks skipped | WSL was off; use Windows Task Scheduler scripts, not WSL cron alone |
| Remote dashboard dead | Re-run `netsh portproxy` after WSL restart |
