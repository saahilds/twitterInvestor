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
powershell -ExecutionPolicy Bypass -File scripts\runner\start_chrome.ps1 -Force
```

On weekdays from 9:30 AM to 4:00 PM the bot starts this Chrome itself. Before 9:30, after 4:00, and on weekends the script leaves Chrome stopped unless you pass `-Force` or the 7:00 AM backfill lock is held. Log into X once in that window. The login stays in the `twitter-bot-chrome` profile across the daily quit.

Register Chrome at logon + scheduled WSL jobs:

```powershell
# Edit RepoPath inside the script if your clone is not /home/<user>/twitterInvestor
powershell -ExecutionPolicy Bypass -File scripts\runner\install_windows_tasks.ps1
```

Verify CDP from WSL (relay port **9223**). Prefer your Windows **LAN** IP (e.g. `192.168.x.x`); the WSL gateway `172.x` is often blocked by Windows Firewall. `start_chrome.ps1` writes the right URL into `.env`:

```bash
grep PLAYWRIGHT_CDP_URL .env
curl -s "$(grep PLAYWRIGHT_CDP_URL .env | cut -d= -f2-)/json/version"
```

If that fails during market hours, re-run `start_chrome.ps1` on Windows (starts Chrome + the CDP relay + updates `.env`). Outside market hours add `-Force`.

### Start the bot

```bash
cd ~/twitterInvestor
./scripts/runner/start.sh
./scripts/runner/status.sh
```

Dashboard on the PC: `http://127.0.0.1:8000/dashboard`

### Unattended mode (travel)

Three layers keep the bot running and tell you when it is not:

| Layer | What it does | Set up with |
|-------|--------------|-------------|
| systemd service + watchdog (Ubuntu) | Starts the bot on boot, restarts it on crash, restarts it when `/health` stops answering or the X feed fails 20 polls in a row (max 3 feed restarts, then one "needs you" alert) | `sudo bash scripts/runner/install_service.sh` |
| Windows tasks | Keep the Ubuntu VM alive. Every 5 min, restart Chrome CDP + the relay if they died during market hours (Mon–Fri 9:30–4:00 ET) or while the 7 AM backfill lock is held. Overnight the task leaves Chrome stopped. | `scripts\runner\install_watchdog_tasks.ps1` |
| Phone alerts | Bot pushes `x_feed_down` / `x_feed_recovered` / `bot_started` / Robinhood auth alerts; watchdog pushes restarts; healthchecks.io alerts when the PC goes silent | `ALERT_WEBHOOK_URL=https://ntfy.sh/<private-topic>`, `HEARTBEAT_PING_URL=<healthchecks ping URL>` |

Once the service is installed, run the bot only through it (not `start_bot.sh` / `runner/start.sh`):

```bash
systemctl status twitter-bot
journalctl -u twitter-bot -f
sudo systemctl restart twitter-bot
journalctl -u twitter-bot-watchdog   # watchdog decisions
```

The dashboard's **Restart bot** button works only under the service.

Before leaving: enable Windows auto-login (the tasks and Chrome need a logged-in desktop after a reboot), pause Windows Update for the trip, and refresh Robinhood auth.

### Tailscale (remote control)

Install Tailscale inside Ubuntu so the dashboard is reachable without port forwarding (the WSL IP changes on reboot):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
tailscale ip -4
```

Install Tailscale on your phone with the same account, then open `http://<ubuntu-tailscale-ip>:8000/dashboard` — Pause, Restart bot, Refresh RH auth. `--ssh` also allows a shell from an SSH app for emergencies.

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
| 9:30 AM–4:00 PM | Worker starts Windows Chrome, loads X, and can trade. After 4:00 PM it quits that Chrome so the login is saved and the RAM is released. |
| 8:00 PM | `scripts/evening_pause.sh` — pause + digest |

Manual:

```bash
./scripts/runner/update_if_behind.sh   # stop → pull → restart → resume
./scripts/runner/stop.sh
./scripts/runner/start.sh
```

## Smoke test after migration

```bash
curl -s "$(grep PLAYWRIGHT_CDP_URL .env | cut -d= -f2-)/json/version" | head
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
| `tweet_fetch_failed` / CDP | Run `start_chrome.ps1`; verify `curl` to `:9223` from WSL. Do **not** `playwright install chrome` in Ubuntu. |
| `Chromium distribution 'chrome' is not found` | Missing `PLAYWRIGHT_CDP_URL` — bot tried to launch Chrome inside WSL. Set CDP URL to `http://<windows-host>:9223`. |
| No tweets | Worker paused → `curl -X POST http://127.0.0.1:8000/resume` |
| RH not logged in | Dashboard Refresh RH auth or `uv run python -m app.cli.rh_login` |
| Scheduled tasks skipped | WSL was off; use Windows Task Scheduler scripts, not WSL cron alone |
| Remote dashboard dead | Re-run `netsh portproxy` after WSL restart |
