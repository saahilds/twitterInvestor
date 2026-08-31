# Twitter/X Signal Trader (Phase 1 MVP)

Minimal, reliability-first trading bot that watches one Twitter/X account, parses rule-based trade signals, applies basic risk checks, and places very small Robinhood orders.

> Safety first: the bot defaults to **simulation mode** and will not place live trades unless explicitly configured.

## Features

- Polls a single X account every 5-10 seconds
- Uses Playwright only for ingestion (no snscrape dependency)
- Supports persistent Chrome profile so login session is reused
- Stores raw tweets in SQLite and deduplicates by tweet ID
- Rule-based parsing using regex + keyword scoring
- Basic risk controls: recognized ticker registry (auto-grows), max trade size, cooldown, duplicate prevention
- Multi-ticker tweets become multiple trades (e.g. add INTC + META, trim ADEA in one post)
- Conviction-based buy sizing as **% of portfolio equity**: tweet allocation % (e.g. `2% port`, `5% weight`) when stated; otherwise standard/reload/thesis tiers scaled by confidence (capped by cash, never margin)
- Weekly human review loop for ambiguous/low-confidence tweets → retrain classifier

- Broker interface with Robinhood + mock implementations
- Structured logging to console and rotating file logs
- FastAPI endpoints for health, tweets, signals, trades, pause/resume
- Async worker loop designed for long-running deployment on Railway or a VPS (see [docs/VPS.md](docs/VPS.md))

## Project Structure

```
app/
  api/
  config/
  db/
  execution/
  ingestion/
  models/
  parsing/
  risk/
  services/
  utils/
tests/
```

## Quickstart (Local)

### Prerequisites

- Python 3.11.x
- Git

---

### Flow A: Using `uv` (recommended)

1. Clone and enter the repo:

   ```bash
   git clone <your-repo-url>
   cd twitterInvestor
   ```

2. Install dependencies:

   ```bash
   uv sync
   ```

3. Install Playwright browsers:

   ```bash
   uv run playwright install chromium
   ```

4. Create local env file:

   ```bash
   cp .env.example .env
   ```

5. Configure `.env` for persistent Chrome login:

   ```dotenv
   SIMULATION_MODE=true
   ENABLE_LIVE_TRADING=false
   TARGET_ACCOUNT=CKCapitalxx
   POLL_INTERVAL_SECONDS=60
   TWITTER_BACKEND=playwright
   PLAYWRIGHT_CHANNEL=chrome
   PLAYWRIGHT_HEADLESS=false
   PLAYWRIGHT_USER_DATA_DIR=.playwright/x-profile
   PLAYWRIGHT_CDP_URL=
   PLAYWRIGHT_REQUIRE_LOGIN=true
   ```

6. (Recommended if login says browser is not secure) attach to your already-open Chrome:

   ```bash
   google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.x-bot-chrome"
   ```

   Then login to X manually in that Chrome window, and set:

   ```dotenv
   PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222
   PLAYWRIGHT_REQUIRE_LOGIN=false
   ```

7. Start the app + worker:

   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   - Without `PLAYWRIGHT_CDP_URL`, the bot launches its own persistent Chrome profile.
   - With `PLAYWRIGHT_CDP_URL`, the bot attaches to your existing Chrome (best for avoiding secure-login restrictions).

7. See [Terminal commands](#terminal-commands) for tests, backfill, dashboard, and account balance.

---

### Flow B: Without `uv` (`venv` + `pip`)

1. Clone and enter the repo:

   ```bash
   git clone <your-repo-url>
   cd twitterInvestor
   ```

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies manually:

   ```bash
   pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings playwright robin-stocks python-dotenv pytest pytest-asyncio
   playwright install chromium
   ```

4. Create local env file:

   ```bash
   cp .env.example .env
   ```

5. Start the app + worker:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

6. See [Terminal commands](#terminal-commands) for tests, backfill, dashboard, and account balance.

---

### Seeing Live Tweets in Your Console

When a **new tweet** is detected, you will now see a log event like:

```json
{
  "message": "tweet_ingested",
  "event_type": "tweet_ingested",
  "context": {
    "tweet_id": "1234567890",
    "account": "CKCapitalxx",
    "tweet_text": "adding NVDA starter here...",
    "posted_at": "2026-05-20T13:42:00+00:00"
  }
}
```

Tips for market session monitoring:

- Keep `LOG_LEVEL=INFO` in `.env`
- Run the server in a dedicated terminal and leave it open during market hours
- At INFO level you will see:
  - `tweet_ingested` (new tweet stored)
  - `trade_executed` or `live_order_submitted` (order placed; failed orders are not logged)
- Rejected signals are still saved in the DB (`parsed_signals.rejection_reason`) but not logged

### Twitter Backend Modes

`TWITTER_BACKEND` options:

- `playwright` (default): use Playwright ingestion
- `mock`: in-memory fake client for local tests

Persistent auth-related env vars:

- `PLAYWRIGHT_USER_DATA_DIR`: profile folder used by persistent browser context
- `PLAYWRIGHT_CHANNEL`: set `chrome` for local Chrome login
- `PLAYWRIGHT_CDP_URL`: attach to an already-running Chrome with remote debugging (recommended when login blocks automated windows)
- `PLAYWRIGHT_HEADLESS=false`: needed for manual login
- `PLAYWRIGHT_REQUIRE_LOGIN=true`: waits for authenticated session on first run
- `PLAYWRIGHT_LOGIN_TIMEOUT_SECONDS`: max wait for manual login completion

### Historical tweet backfill

The live worker only ingests tweets visible on the current profile page. See [Terminal commands — Backfill tweets](#backfill-tweets) for one-off backfill commands.

### P&L by ticker

Trades in SQLite are the source of truth. See [Terminal commands — P&L](#pnl) and [Dashboard & health](#dashboard--health) for CLI and UI access.

- **Realized P&L**: from SELL trades vs average cost of shares held.
- **Unrealized P&L**: open shares × (last price − avg cost).
- Use `--live-only` / `?live_only=true` to exclude simulated trades.

## Terminal commands

Quick reference for day-to-day operations. Primary examples use `uv run`; without `uv`, use `python -m` and `pytest` from your activated venv.

### Run the bot

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

VPS (Docker):

```bash
./scripts/start_bot.sh
./scripts/stop_bot.sh
```

### Dashboard & health

- **Dashboard UI:** [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard) (auto-refreshes every 15s)
- **Health check:**

  ```bash
  curl http://127.0.0.1:8000/health
  ```

- **JSON snapshot:** `GET /dashboard/data`, `GET /portfolio/pnl`

The dashboard shows bot status, Robinhood holdings, P&amp;L by ticker, today's digest, recent tweets/trades (with Wrong-label buttons), pause/resume, and a **Refresh RH auth** button (approve the push in the Robinhood app).

Robinhood device approval typically lasts ~7 days. The dashboard **RH auth** chip tracks age (default max **6 days**, warn at **1 day** remaining). While traveling, tap **Refresh RH auth**, then approve in the Robinhood mobile app.

```bash
# Optional CLI / cron backup (same flow as the dashboard button)
./scripts/rh_reauth.sh
```

Env knobs: `ROBINHOOD_PICKLE_MAX_AGE_DAYS=6`, `ROBINHOOD_PICKLE_WARN_DAYS=1`, `ROBINHOOD_REAUTH_TIMEOUT_SECONDS=180`.

### Account balance (Robinhood)

Verify login and view balances (no order placed). The bot sizes buys from **cash only**, not buying power (margin).

```bash
uv run python -m app.cli.rh_login --list-accounts
uv run python -m app.cli.rh_login --verify-all-accounts
uv run python -m app.cli.rh_login
```

Set `ROBINHOOD_ACCOUNT=individual`, `joint`, or an exact `account_number` from `--list-accounts`.

### P&L

```bash
uv run python -m app.cli.pnl
uv run python -m app.cli.pnl --live-only
uv run python -m app.cli.pnl --json
```

### Test a single order

During regular market hours only:

```bash
uv run python -m app.cli.test_order --ticker SPY --amount 1.0
```

Pre-checks available cash before submitting (live mode).

### Pause / resume

```bash
curl -X POST http://127.0.0.1:8000/pause
curl -X POST http://127.0.0.1:8000/resume
```

### Backfill tweets

**Full calendar year** (large 2026 backfills):

```bash
uv run python -m app.scripts.backfill_tweets --year 2026 --max-tweets 12000 --max-scrolls 2500 --include-replies --include-retweets
```

**Since a start date** (lighter scroll):

```bash
uv run python -m app.cli.backfill --since 2026-01-01
```

`app.cli.backfill` options: `--since` (default `2026-01-01`), `--max-scrolls`, `--scroll-pause-ms`. Replies/retweets follow `IGNORE_REPLIES` / `IGNORE_RETWEETS` unless overridden on the year script.

### Tests

```bash
uv run pytest
```

### Parser replay / feedback / daily digest

```bash
# Re-run parser (+ risk) over stored tweets; --compare-stored shows drift vs historical decisions
uv run python -m app.cli.replay --since 2026-01-01 --compare-stored
uv run python -m app.cli.replay --only-mismatches --assume-cash 5000 --json

# Export Wrong-labels from the dashboard (training only — does not affect live orders)
uv run python -m app.cli.export_feedback --out data/feedback.jsonl
uv run python -m app.cli.export_feedback --apply

# Rebuild / finalize progressive daily digest (DB rollup only; no X or Robinhood fetch)
uv run python -m app.cli.daily_summary --date 2026-07-24
uv run python -m app.cli.daily_summary --finalize
```

Daily digest summarizes **trade alerts** (BUY/SELL) and **executed trades** only — rebuilt from the DB through the day. Evening pause finalizes at 8 PM ET (`POST /digest/finalize`). Optional webhook: set `ALERT_WEBHOOK_URL`.

### Inspect DB / API

```bash
sqlite3 trading_bot.db
curl "http://127.0.0.1:8000/tweets?limit=200"
curl "http://127.0.0.1:8000/trades?limit=50"
curl "http://127.0.0.1:8000/signals?limit=50"
```

## API Endpoints

- `GET /health`
- `POST /robinhood/reauth` — start force login; approve push in Robinhood app
- `GET /robinhood/reauth/status` — `idle` | `awaiting_approval` | `succeeded` | `failed`
- `GET /tweets?limit=50`
- `GET /signals?limit=50`
- `GET /portfolio/pnl` — realized + unrealized P&L by ticker (live Robinhood quotes, ~60s cache)
- `GET /dashboard` — live UI (status, P&amp;L, tweets, trades; refreshes every 15s)
- `GET /dashboard/data` — JSON snapshot for the dashboard
- `GET /trades?limit=50` — execution status, limit/fill price, quantity, broker order id, errors
- `GET /trades/{trade_id}`
- `POST /trades/{trade_id}/refresh` — pull latest Robinhood order state (filled/cancelled/open)
- `POST /pause`
- `POST /resume`

## Safety Model

- `SIMULATION_MODE=true` by default.
- Live trading requires both:
  - `ENABLE_LIVE_TRADING=true`
  - `SIMULATION_MODE=false`
- Buy sizing is **portfolio-relative** (% of `CK_PORTFOLIO_USD`, the CKCapital sleeve — not full Robinhood equity). Explicit tweet allocations are authoritative and capped only by available cash; otherwise conviction/watchlist weighting applies. Explicit sell fractions and “trimmed down to X%” targets are authoritative and capped only by shares owned; fallback sells cap at `MAX_SELL_NOTIONAL_PCT` of the CK sleeve.
- Any US ticker in a parsed **BUY**/**SELL** signal can trade (no allowlist). **SELL** still requires an open Robinhood position.
- `KNOWN_TICKERS` is an optional parser-only hint for bare symbols without `$`; it never gates trading.

### Live trading test checklist (market hours)

1. Set in `.env`:

   ```env
   SIMULATION_MODE=false
   ENABLE_LIVE_TRADING=true
   BROKER_BACKEND=robinhood
   ORDER_EXECUTION_MODE=limit_at_ask
   DEFAULT_BUY_ALLOCATION_PCT=1.0
   RELOAD_BUY_ALLOCATION_PCT_MAX=5.0
   THESIS_BUY_ALLOCATION_PCT_MIN=3.0
   THESIS_BUY_ALLOCATION_PCT_MAX=7.0
   CK_PORTFOLIO_USD=10000.0
   TRADING_WINDOW_ENABLED=true
   US_SYMBOLS_ONLY=true
   ROBINHOOD_USERNAME=...
   ROBINHOOD_PASSWORD=...
   # ROBINHOOD_MFA_SECRET=...  # if 2FA enabled
   ```

2. Restart the app after changing `.env` (settings load at startup).
3. During regular hours (Mon–Fri 9:30–16:00 ET), verify:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

   Expect `live_trading_enabled: true` and `within_market_hours: true`.

4. Verify Robinhood login and account balance — see [Terminal commands — Account balance](#account-balance-robinhood).
5. Pre-flight a single order during market hours (optional) — see [Terminal commands — Test a single order](#test-a-single-order).
6. Start the bot and monitor `logs/bot.log`, `GET /trades`, `GET /signals`.
7. Use `POST /pause` to stop new orders immediately — see [Terminal commands — Pause / resume](#pause--resume).

BUY signals place a **limit buy at the ask** (or fractional market per `ORDER_EXECUTION_MODE`). **SELL** signals sell a **fraction of the live position** (trim ≈ 25%, half = 50%, closed/sell = 100%, or explicit `%` in the tweet) only when the ticker is held in Robinhood. Guards: US symbols only, market hours, one trade per tweet, one per ticker per US day, 5-minute cooldown.

## Weekly signal labeling

Once a week, review ambiguous tweets and feed truth labels back into the model:

```bash
# Export review queue (default: last 7 days, max 25 rows)
uv run python -m app.scripts.weekly_review

# Edit data/reviews/review-YYYY-MM-DD.jsonl → set truth_action (BUY/SELL/WATCH/IGNORE)
uv run python -m app.scripts.ingest_labels data/reviews/review-YYYY-MM-DD.jsonl

# Or label interactively:
uv run python -m app.scripts.weekly_review --interactive

# Retrain + print before/after metrics
uv run python -m app.scripts.retrain_from_labels

# Metrics / threshold sweep
uv run python -m app.scripts.eval_classifier --cv
uv run python -m app.scripts.eval_classifier --sweep
```

Optional weak labels from filled live trades: `uv run python -m app.scripts.outcome_weak_labels --apply`.

## Local Mac schedule (8 AM – 6 PM ET)

Run the bot on your Mac daily without leaving it on 24/7: **[docs/LOCAL_MAC.md](docs/LOCAL_MAC.md)**.

```bash
./scripts/local/install_schedule.sh   # launchd: start 8 AM, stop 6 PM Eastern
./scripts/local/start_bot.sh          # manual start
./scripts/local/stop_bot.sh           # manual stop
```

## VPS deployment (Hetzner)

For production on Hetzner CX32 (extended hours, morning backfill, Caddy HTTPS dashboard): **[docs/VPS.md](docs/VPS.md)**.

For a **home Windows 10 PC** as the 24/7 runner (WSL2 + uv, Tailscale dashboard): **[docs/HOME_RUNNER.md](docs/HOME_RUNNER.md)**.

Quick start on the server:

```bash
cp .env.example .env   # then edit / scp from Mac
./scripts/start_bot.sh
```

## Railway Deployment

The repo includes a `Dockerfile` and `railway.json`.

Railway environment variables to configure:

- `TARGET_ACCOUNT` (defaults to `CKCapitalxx`)
- `TWITTER_BACKEND=playwright`
- `PLAYWRIGHT_CHANNEL=chromium`
- `PLAYWRIGHT_HEADLESS=true`
- `PLAYWRIGHT_REQUIRE_LOGIN=false`
- `SIMULATION_MODE` (keep `true` until confident)
- `ENABLE_LIVE_TRADING`
- `ROBINHOOD_USERNAME`, `ROBINHOOD_PASSWORD` (only for live)
- Risk settings (`DEFAULT_BUY_ALLOCATION_PCT`, `COOLDOWN_SECONDS`, etc.)

Deploy steps:

1. Push this repo to GitHub.
2. Create a Railway service from the repo.
3. Set environment variables in Railway.
4. Deploy.

## Important Notes

- This is an MVP for controlled experimentation, not institutional-grade infrastructure.
- Start in simulation and inspect logs + DB records before enabling live mode.
- TODO: add stronger auth/session handling for Robinhood and open-order lifecycle tooling.
