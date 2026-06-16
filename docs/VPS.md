# VPS deployment (Hetzner CX32)

Deploy the trading bot on a **Hetzner Cloud CX32** in **Ashburn (US)** with Docker Compose, extended-hours scheduling (7 AM–8 PM ET), morning tweet backfill, and a phone-friendly HTTPS dashboard behind Caddy basic auth.

**Chosen setup**

| Item | Value |
|------|--------|
| Provider | Hetzner Cloud CX32 (8 GB / 4 vCPU, ~$11/mo) |
| Region | Ashburn, VA |
| OS | Ubuntu 24.04 LTS |
| Schedule | **B1:** VM 24/7; worker **pause 8 PM / resume 7 AM ET** Mon–Fri |
| Trades | `TRADING_WINDOW_ENABLED=true` → 9:30 AM–4 PM ET only |
| Dashboard | Caddy HTTPS + basic auth → `127.0.0.1:8000` |

---

## Detailed Hetzner setup (step-by-step)

Follow these steps in order on your **Mac** and on the **VPS**. Replace placeholders:

| Placeholder | Example |
|-------------|---------|
| `YOUR_VPS_IP` | `5.161.123.45` |
| `YOUR_GITHUB_USER` | your GitHub username |
| `bot.example.com` | subdomain you own |

### Step 0 — Prerequisites on your Mac

1. **Bot works locally** — you can run `uv run uvicorn app.main:app` and ingest tweets in simulation mode.
2. **X is logged in locally** — `.playwright/x-profile` exists and can load the target account without manual login (`PLAYWRIGHT_REQUIRE_LOGIN=false` locally after first login).
3. **SSH key** (create if you don't have one):

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -C "your-email@example.com"
cat ~/.ssh/id_ed25519.pub   # copy this for Hetzner
```

4. **`.env` ready** — copy from example and fill in Robinhood credentials (keep locally; you'll scp to VPS):

```bash
cd ~/Documents/GitHub/twitterInvestor   # your repo path
cp .env.example .env
nano .env   # or your editor
```

5. **Optional but recommended:** a domain name (e.g. `bot.yourdomain.com`) for HTTPS on your phone. You can skip domain for week 1 and use SSH tunnel only.

---

### Step 1 — Create Hetzner account and project

1. Go to [console.hetzner.cloud](https://console.hetzner.cloud/) and sign up (credit card required; CX32 is ~€7.59/mo).
2. Create a **New Project** (e.g. `twitter-bot`).
3. In the project, open **Security → SSH keys → Add SSH key**:
   - Name: `macbook`
   - Public key: paste output of `cat ~/.ssh/id_ed25519.pub`
4. Save the key — you'll select it when creating the server.

---

### Step 2 — Create firewall (before the server)

Creating the firewall first lets you attach it during server creation.

1. **Firewalls → Create Firewall**
2. Name: `twitter-bot-fw`
3. **Inbound rules** (add these; order matters — Hetzner applies all matching allow rules):

| Source | Protocol | Port | Purpose |
|--------|----------|------|---------|
| Your home IP/32 (find at [ifconfig.me](https://ifconfig.me)) | TCP | 22 | SSH from your Mac only |
| Any IPv4 / Any IPv6 | TCP | 80 | Caddy HTTP (Let's Encrypt) |
| Any IPv4 / Any IPv6 | TCP | 443 | Caddy HTTPS dashboard |

4. **Outbound:** leave default (allow all) — bot needs Robinhood, X, Docker Hub, etc.
5. Do **not** add a rule for port 8000 — uvicorn stays on localhost only.
6. Create the firewall (don't attach yet if the UI asks; you'll attach on the server).

**Tip:** If your home IP changes, update the SSH rule in Hetzner console or temporarily allow `0.0.0.0/0` on 22 (less secure).

---

### Step 3 — Create the CX32 server

1. **Servers → Add Server**
2. **Location:** `Ashburn, VA` (US) — closest US region for Robinhood latency
3. **Image:** `Ubuntu 24.04`
4. **Type:** **Shared vCPU → CX32** (4 vCPU, 8 GB RAM, 80 GB disk)
5. **Networking:** IPv4 + IPv6 (default)
6. **SSH keys:** select your `macbook` key
7. **Volumes:** skip for v1 (80 GB root disk is enough)
8. **Firewalls:** attach `twitter-bot-fw`
9. **Backups:** optional (~20% extra); snapshots contain `.env` + RH pickle — treat as secrets
10. **Name:** `twitter-bot`
11. **Create & Buy now**

Wait ~30 seconds. Copy the **IPv4 address** from the server overview — that's `YOUR_VPS_IP`.

First login from Mac:

```bash
ssh root@YOUR_VPS_IP
```

If prompted about host key, type `yes`. You should land in a root shell on Ubuntu 24.04.

---

### Step 4 — Initial server hardening (on VPS)

Run as `root` on the VPS:

```bash
apt-get update && apt-get upgrade -y
timedatectl set-timezone America/New_York   # matches trading schedule
```

Optional: create a non-root user (Hetzner docs often use `root` for simplicity; either works):

```bash
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
```

If you use `deploy`, replace `root` with `deploy` in paths below and use `sudo` where needed.

Verify outbound internet:

```bash
curl -sI https://github.com | head -1
# HTTP/2 200
```

---

### Step 5 — Install Docker (on VPS)

```bash
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo ${VERSION_CODENAME}) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Verify:

```bash
docker --version          # Docker version 2x...
docker compose version    # Docker Compose version v2...
docker run --rm hello-world
```

First `hello-world` pull may take a minute.

---

### Step 6 — Clone repo and create data directories (on VPS)

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/YOUR_GITHUB_USER/twitterInvestor.git
cd twitterInvestor
mkdir -p data/logs data/x-profile data/rh-tokens
touch data/trading_bot.db
chmod 700 data
chmod +x scripts/*.sh
```

If the repo is private, use a deploy key or HTTPS with a personal access token:

```bash
git clone https://YOUR_TOKEN@github.com/YOUR_GITHUB_USER/twitterInvestor.git
```

---

### Step 7 — Copy secrets from Mac to VPS

**On your Mac** (from repo root):

```bash
# .env (never commit this)
scp .env root@YOUR_VPS_IP:/opt/twitterInvestor/.env
ssh root@YOUR_VPS_IP 'chmod 600 /opt/twitterInvestor/.env'

# X login session (can be large — 100MB+; may take a few minutes)
rsync -av --progress .playwright/x-profile/ root@YOUR_VPS_IP:/opt/twitterInvestor/data/x-profile/
```

**On VPS**, fix permissions:

```bash
chmod -R u+rwX /opt/twitterInvestor/data/x-profile
ls -la /opt/twitterInvestor/data/x-profile | head   # should show Chrome profile dirs
```

**Optional:** copy existing local DB if you want historical tweets on VPS:

```bash
# Mac
scp trading_bot.db root@YOUR_VPS_IP:/opt/twitterInvestor/data/trading_bot.db
```

---

### Step 8 — Configure `.env` for VPS (on VPS)

Edit `/opt/twitterInvestor/.env`. These differ from your Mac settings:

```dotenv
# Playwright — VPS/Docker uses bundled Chromium, not Mac Chrome
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_CHANNEL=chromium
PLAYWRIGHT_CDP_URL=
PLAYWRIGHT_REQUIRE_LOGIN=false
PLAYWRIGHT_USER_DATA_DIR=.playwright/x-profile

# Phase 1: simulation only
SIMULATION_MODE=true
ENABLE_LIVE_TRADING=false
TRADING_WINDOW_ENABLED=true

# Robinhood — fill in before phase 2
ROBINHOOD_USERNAME=your@email.com
ROBINHOOD_PASSWORD=your-password
ROBINHOOD_ACCOUNT=individual
# Do NOT set ROBINHOOD_MFA_SECRET — push-only MFA on phone

TARGET_ACCOUNT=CKCapitalxx
POLL_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
LOG_FILE=logs/bot.log
```

Save and confirm permissions: `chmod 600 .env`.

---

### Step 9 — Build and start the bot (on VPS)

```bash
cd /opt/twitterInvestor
./scripts/start_bot.sh
```

First run builds the Docker image (5–15 minutes — installs Chromium and Python deps). Watch logs:

```bash
docker compose logs -f bot
```

Wait until you see uvicorn started and worker messages. Ctrl+C to leave logs.

**Health check:**

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Expect something like:

- `"simulation_mode": true`
- `"worker_paused": false` (or true if outside hours — use `curl -X POST http://127.0.0.1:8000/resume` to test)
- `"robinhood_logged_in": false` (until phase 2)

**Tweet check** (after resume, during extended hours):

```bash
curl -s "http://127.0.0.1:8000/tweets?limit=3" | python3 -m json.tool
docker compose logs bot 2>&1 | grep tweet_ingested | tail -5
```

If no tweets: worker may be paused → `curl -X POST http://127.0.0.1:8000/resume`. X profile may be invalid → re-rsync from Mac.

---

### Step 10 — Install cron schedule (on VPS)

Extended hours: **7 AM resume + backfill**, **8 PM pause** (Mon–Fri, Eastern).

```bash
crontab -e
```

Paste (paths must match):

```
CRON_TZ=America/New_York

0 7 * * 1-5 /opt/twitterInvestor/scripts/startup_backfill.sh >> /opt/twitterInvestor/data/logs/cron.log 2>&1
0 20 * * 1-5 /opt/twitterInvestor/scripts/evening_pause.sh >> /opt/twitterInvestor/data/logs/cron.log 2>&1
```

Test manually:

```bash
cd /opt/twitterInvestor
./scripts/evening_pause.sh
curl -s http://127.0.0.1:8000/health | grep worker_paused
./scripts/startup_backfill.sh   # runs backfill + resume (may take several minutes)
```

Optional — start container on reboot:

```bash
cp /opt/twitterInvestor/deploy/systemd/twitter-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable twitter-bot.service
```

---

### Step 11 — Domain DNS (for phone dashboard)

1. At your domain registrar (Cloudflare, Namecheap, etc.), add an **A record**:
   - Name: `bot` (or `@` if using apex)
   - Value: `YOUR_VPS_IP`
   - TTL: 300 (or Auto)
2. Wait for DNS propagation (often 1–15 minutes):

```bash
dig +short bot.example.com
# should return YOUR_VPS_IP
```

You can defer this until phase 3 and use an SSH tunnel meanwhile:

```bash
# Mac — temporary dashboard access without public HTTPS
ssh -L 8000:127.0.0.1:8000 root@YOUR_VPS_IP
# open http://127.0.0.1:8000/dashboard on Mac
```

---

### Step 12 — Caddy HTTPS + basic auth (on VPS)

**Only do this before exposing the dashboard to the internet.** The app has no login of its own.

Install Caddy:

```bash
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy
```

Create dashboard password (pick a strong password):

```bash
caddy hash-password --plaintext 'YourStrongDashboardPassword'
# copy the hash output (starts with JDJh...)
```

Edit Caddy config:

```bash
nano /etc/caddy/Caddyfile
```

Replace entire file:

```
bot.example.com {
	encode gzip
	basicauth {
		admin PASTE_HASH_HERE
	}
	reverse_proxy 127.0.0.1:8000
}
```

- `admin` is the username you'll type in the browser
- `PASTE_HASH_HERE` is the hash from `caddy hash-password` (not the plaintext password)

Enable and test:

```bash
systemctl enable caddy
systemctl reload caddy
systemctl status caddy
```

On your phone (cellular, not Wi‑Fi — confirms public access):

1. Open `https://bot.example.com/dashboard`
2. Enter basic auth user `admin` + your password
3. Confirm bot status, tweets, pause/resume buttons work

Caddy obtains Let's Encrypt certificates automatically on first HTTPS request (ports 80/443 must be open — firewall step 2).

---

### Step 13 — Robinhood push-MFA seed (phase 2)

When simulation looks good and you're ready for RH dashboard data:

1. Ensure `ROBINHOOD_USERNAME` / `ROBINHOOD_PASSWORD` / `ROBINHOOD_ACCOUNT` are in `.env`
2. Restart to pick up env changes:

```bash
cd /opt/twitterInvestor
docker compose restart bot
```

3. Run login interactively (have your phone ready):

```bash
docker compose exec -it bot uv run python -m app.cli.rh_login
```

4. **Approve the push notification on your Robinhood app** when prompted
5. Verify:

```bash
docker compose exec -T bot uv run python -m app.cli.rh_login --verify-all-accounts
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Look for `"robinhood_logged_in": true`.

6. Confirm pickle persisted:

```bash
ls -la /opt/twitterInvestor/data/rh-tokens/
docker compose restart bot
curl -s http://127.0.0.1:8000/health | grep robinhood_logged_in
# should still be true after restart
```

**If login fails with 429:** wait 15 minutes; don't retry repeatedly. Session manager backs off automatically.

**If push never arrives:** check RH app notifications; try `rh_login` again once after cooldown.

---

### Step 14 — Go live (phase 3)

When RH verify works and you want real orders:

1. Edit `.env` on VPS:

```dotenv
SIMULATION_MODE=false
ENABLE_LIVE_TRADING=true
DEFAULT_TRADE_SIZE_USD=1.0
MAX_TRADE_SIZE_USD=1.0
TRADING_WINDOW_ENABLED=true
```

2. Restart:

```bash
docker compose restart bot
```

3. Confirm during **market hours** (Mon–Fri 9:30 AM–4 PM ET):

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
# live_trading_enabled: true, within_market_hours: true
```

4. Monitor from phone dashboard or:

```bash
tail -f /opt/twitterInvestor/data/logs/bot.log
curl -s http://127.0.0.1:8000/trades?limit=5 | python3 -m json.tool
```

5. **Emergency stop:** `curl -X POST http://127.0.0.1:8000/pause` or use Pause on the dashboard

---

### Step 15 — Ongoing maintenance

| Task | Command |
|------|---------|
| View logs | `docker compose logs -f bot` |
| Update code | `cd /opt/twitterInvestor && git pull && ./scripts/start_bot.sh` |
| Re-auth RH | `docker compose exec -it bot uv run python -m app.cli.rh_login` |
| Re-sync X profile | `rsync` from Mac (step 7) |
| Reboot server | Hetzner console or `reboot` — systemd/cron bring bot back |

**Never run:** `docker compose down -v` (deletes RH pickle + X profile mounts).

**Backups:** Hetzner snapshot the whole server, or copy `data/` off-site:

```bash
# Mac — pull backup
rsync -av root@YOUR_VPS_IP:/opt/twitterInvestor/data/ ./vps-backup/
```

---

### Quick verification checklist

| # | Check | How |
|---|-------|-----|
| 1 | SSH works | `ssh root@YOUR_VPS_IP` |
| 2 | Firewall blocks 8000 publicly | From Mac: `curl --connect-timeout 3 http://YOUR_VPS_IP:8000` should **fail** |
| 3 | Container running | `docker compose ps` → `bot` Up |
| 4 | Health OK | `curl -s http://127.0.0.1:8000/health` on VPS |
| 5 | Tweets ingesting | `grep tweet_ingested data/logs/bot.log` |
| 6 | Cron installed | `crontab -l` shows 7 AM / 8 PM lines |
| 7 | HTTPS dashboard | Phone → `https://bot.example.com/dashboard` |
| 8 | RH logged in | `/health` → `robinhood_logged_in: true` |
| 9 | Pickle survives restart | restart container, RH still logged in |
| 10 | Live mode gated | trades only 9:30–4 ET with `TRADING_WINDOW_ENABLED=true` |

---

## Architecture

```
Phone ──HTTPS──► Caddy (:443, basic auth)
                    └──► uvicorn (:8000, localhost only)
                              ├── BotWorker (X poll)
                              ├── SQLite (data/trading_bot.db)
                              ├── Playwright profile (data/x-profile)
                              └── RH pickle (data/rh-tokens → /root/.tokens)
```

Repo files used on the VPS:

| Path | Purpose |
|------|---------|
| `docker-compose.yml` | App container + bind mounts |
| `scripts/start_bot.sh` | `docker compose up -d` (never `down -v`) |
| `scripts/stop_bot.sh` | `docker compose stop` |
| `scripts/startup_backfill.sh` | 7 AM backfill + `POST /resume` |
| `scripts/evening_pause.sh` | 8 PM `POST /pause` |
| `deploy/caddy/Caddyfile` | HTTPS reverse proxy template |
| `deploy/cron/twitter-bot.cron` | Cron examples (America/New_York) |
| `deploy/systemd/twitter-bot.service` | Optional boot-time compose start |

---

## 1. Create the Hetzner server

1. [Hetzner Cloud Console](https://console.hetzner.cloud/) → **Add Server**
2. **Location:** Ashburn (US)
3. **Image:** Ubuntu 24.04
4. **Type:** CX32 (8 GB RAM)
5. **SSH key:** add your public key (password login off)
6. **Firewall (recommended):**
   - Inbound **22/tcp** — your home IP if possible
   - Inbound **80/tcp**, **443/tcp** — anywhere (Caddy / Let's Encrypt)
   - Deny all other inbound
7. Note the server IP; optional: point `bot.example.com` A record at it.

---

## 2. Server bootstrap

SSH in as root (or your user):

```bash
ssh root@YOUR_VPS_IP
```

Install Docker (Ubuntu 24.04):

```bash
apt-get update && apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo ${VERSION_CODENAME}) stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Clone the repo:

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/YOUR_USER/twitterInvestor.git
cd twitterInvestor
```

Create persistent data dirs (also created by `start_bot.sh`):

```bash
mkdir -p data/logs data/x-profile data/rh-tokens
touch data/trading_bot.db
chmod 700 data
```

---

## 3. Secrets and `.env`

**Never commit** `.env`, `data/x-profile`, `data/rh-tokens`, or `data/trading_bot.db`.

From your Mac:

```bash
scp .env root@YOUR_VPS_IP:/opt/twitterInvestor/.env
ssh root@YOUR_VPS_IP 'chmod 600 /opt/twitterInvestor/.env'
```

Edit `/opt/twitterInvestor/.env` on the VPS for production. Minimum VPS overrides (see `.env.example` **VPS** section):

```dotenv
PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_CHANNEL=chromium
PLAYWRIGHT_CDP_URL=
PLAYWRIGHT_REQUIRE_LOGIN=false
PLAYWRIGHT_USER_DATA_DIR=.playwright/x-profile

SIMULATION_MODE=true
ENABLE_LIVE_TRADING=false
TRADING_WINDOW_ENABLED=true
```

**Sensitive paths on disk**

| Secret | Host path | Container path |
|--------|-----------|----------------|
| `.env` | `/opt/twitterInvestor/.env` | env_file |
| RH session pickle | `data/rh-tokens/` | `/root/.tokens` |
| X login profile | `data/x-profile/` | `/app/.playwright/x-profile` |
| SQLite DB | `data/trading_bot.db` | `/app/trading_bot.db` |
| Logs | `data/logs/` | `/app/logs` |

Hetzner snapshots include all of the above — treat snapshots as secret storage.

---

## 4. Seed X (Twitter) profile (one-time)

Log in to X on your Mac using the bot's persistent profile, then copy it to the VPS.

On Mac (from repo root):

```bash
# Ensure you have a logged-in session in .playwright/x-profile locally first.
rsync -av --delete .playwright/x-profile/ root@YOUR_VPS_IP:/opt/twitterInvestor/data/x-profile/
```

On VPS, verify ownership and that the container can read it:

```bash
chmod -R u+rwX /opt/twitterInvestor/data/x-profile
```

Env on VPS:

- `PLAYWRIGHT_HEADLESS=true`
- `PLAYWRIGHT_REQUIRE_LOGIN=false` (profile already logged in)
- `PLAYWRIGHT_CDP_URL=` empty (no Mac Chrome attach on VPS)
- `PLAYWRIGHT_CHANNEL=chromium` (Dockerfile installs Chromium; overrides Mac `chrome`)

If X logs you out later, re-copy the profile from Mac or re-login locally and rsync again.

---

## 5. Start the bot (simulation first)

```bash
cd /opt/twitterInvestor
./scripts/start_bot.sh
docker compose logs -f bot
```

Check health (on VPS):

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Expect the worker running, simulation mode, tweets ingesting during extended hours after resume.

**Never run** `docker compose down -v` — `-v` deletes RH pickle and X profile volumes.

Stop without destroying data:

```bash
./scripts/stop_bot.sh
```

---

## 6. Robinhood push-MFA seeding

This account uses **push approval only** (no TOTP). Leave `ROBINHOOD_MFA_SECRET` unset.

### One-time seed on VPS

1. Set `ROBINHOOD_USERNAME`, `ROBINHOOD_PASSWORD`, `ROBINHOOD_ACCOUNT` in `.env`.
2. Run login **inside** the container (pickle persists to `data/rh-tokens`):

```bash
cd /opt/twitterInvestor
docker compose exec -it bot uv run python -m app.cli.rh_login
```

3. Approve the push notification on your phone **once**.
4. Verify:

```bash
docker compose exec -T bot uv run python -m app.cli.rh_login --verify-all-accounts
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Health fields to watch:

- `robinhood_logged_in` — should be `true`
- `robinhood_auth_error` — `null` when healthy
- `robinhood_auth_retry_in_seconds` — backoff after failures

### Pickle lifetime and B1 schedule

- robin_stocks stores session data in `/root/.tokens` (mounted at `data/rh-tokens`).
- Sessions often last **days to weeks** if the VM stays up and you avoid `down -v`.
- **B1 schedule** (pause worker overnight, VM stays on) avoids cold boots that trigger re-login.

### When pickle expires while you are away

1. Trades and RH dashboard holdings stop updating; tweet ingestion can continue (X is separate).
2. Recovery when you can access your phone:

```bash
docker compose exec -it bot uv run python -m app.cli.rh_login --verify-all-accounts
```

Approve push once → confirm `/health` shows `robinhood_logged_in: true`.

Keep `ROBINHOOD_LOGIN_RETRY_SECONDS` and `ROBINHOOD_LOGIN_429_BACKOFF_SECONDS` set (defaults in `.env.example`) to avoid approval spam on failed logins.

---

## 7. Caddy HTTPS + basic auth (phone dashboard)

The app has **no built-in auth** on `/dashboard`, `/pause`, or `/resume`. Caddy must protect all routes before exposing port 443.

Install Caddy on the host (not in Docker):

```bash
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy
```

Generate a password hash:

```bash
caddy hash-password --plaintext 'your-strong-password'
```

Create `/etc/caddy/Caddyfile` from `deploy/caddy/Caddyfile`, replacing `example.com` with your domain and the hash:

```
bot.example.com {
	encode gzip
	basicauth {
		admin JDJhJDEwJ...   # output of caddy hash-password
	}
	reverse_proxy 127.0.0.1:8000
}
```

```bash
systemctl reload caddy
```

Open `https://bot.example.com/dashboard` on your phone; use the basic-auth user/password. Pause/resume work through the same authenticated origin.

Optional: Cloudflare Tunnel instead of open 443 (no port forwarding; add Cloudflare Access for OTP).

---

## 8. Extended-hours schedule (cron)

**Behavior (Mon–Fri, America/New_York)**

| Time | Action |
|------|--------|
| **7:00 AM** | `startup_backfill.sh` — backfill since last tweet (or since previous 8 PM), then `POST /resume` |
| **7 AM–8 PM** | Worker polls X; trades only 9:30 AM–4 PM if `TRADING_WINDOW_ENABLED=true` |
| **8:00 PM** | `evening_pause.sh` — `POST /pause` (API/dashboard stay up) |
| **Weekends** | Worker paused; Monday 7 AM backfill covers since Friday 8 PM |

Install cron as root (or a dedicated user in `/opt/twitterInvestor`):

```bash
crontab -e
```

Paste from `deploy/cron/twitter-bot.cron`, adjusting paths if needed.

Manual test:

```bash
cd /opt/twitterInvestor
./scripts/startup_backfill.sh
./scripts/evening_pause.sh
```

---

## 9. Optional: systemd on boot

```bash
cp /opt/twitterInvestor/deploy/systemd/twitter-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable twitter-bot.service
systemctl start twitter-bot.service
```

Cron still handles 7 AM backfill/resume and 8 PM pause; systemd only ensures the container is up after reboot.

---

## 10. Rollout phases

### Phase 1 — Simulation on VPS (week 1)

- Deploy with `SIMULATION_MODE=true`, `ENABLE_LIVE_TRADING=false`
- Seed X profile (section 4)
- `./scripts/start_bot.sh`; confirm tweets in DB: `curl http://127.0.0.1:8000/tweets?limit=5`
- Install cron; verify Monday 7 AM backfill in logs
- Caddy **not** required yet (SSH tunnel or localhost only)

### Phase 2 — Robinhood verify (week 2)

- `docker compose exec -it bot uv run python -m app.cli.rh_login` + phone approve
- `/health` → `robinhood_logged_in: true`
- Dashboard shows holdings (still simulation for bot trades)
- Confirm `data/rh-tokens` persists across `docker compose restart bot`

### Phase 3 — Live small size + phone dashboard (week 3)

- Set `SIMULATION_MODE=false`, `ENABLE_LIVE_TRADING=true`, small `MAX_TRADE_SIZE_USD`
- Caddy HTTPS + basic auth live; test `/dashboard` from phone
- Market hours: confirm one controlled live fill via logs / `GET /trades`
- Keep `POST /pause` reachable only behind Caddy auth

### Later (optional)

- Uptime monitor on `/health` when `robinhood_logged_in` is false during market hours
- API token for pause/resume (app-level auth)
- In-app `WORKER_SCHEDULE_*` env vars instead of cron

---

## 11. Operations cheat sheet

```bash
cd /opt/twitterInvestor

# Logs
docker compose logs -f bot
tail -f data/logs/bot.log

# Restart app (keeps volumes)
docker compose restart bot

# Backfill manually
docker compose exec -T bot uv run python -m app.cli.backfill --since 2026-05-01

# RH login / re-auth
docker compose exec -it bot uv run python -m app.cli.rh_login

# Pause / resume
curl -X POST http://127.0.0.1:8000/pause
curl -X POST http://127.0.0.1:8000/resume

# Update code
git pull && ./scripts/start_bot.sh
```

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No tweets ingested | Worker paused or X profile expired | `POST /resume`; re-rsync X profile |
| RH 429 / login loop | Expired pickle + repeated logins | Wait for backoff; run `rh_login` once manually |
| `robinhood_logged_in: false` | Push MFA needed | SSH in, `rh_login`, approve on phone |
| Playwright OOM | RAM pressure | CX32 (8 GB) recommended; `shm_size: 1gb` in compose |
| Dashboard public | Caddy not configured | Never expose `8000` publicly; use Caddy basic auth |
| Data lost after deploy | Used `down -v` | Restore from backup; re-seed RH + X |

---

## Cost note

On Hetzner, **pausing the worker does not reduce the monthly bill** — you pay for the server 24/7. B1 saves CPU/RAM and avoids overnight Playwright, not VPS fees (~$11/mo for CX32).
