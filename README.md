# Twitter/X Signal Trader (Phase 1 MVP)

Minimal, reliability-first trading bot that watches one Twitter/X account, parses rule-based trade signals, applies basic risk checks, and places very small Robinhood orders.

> Safety first: the bot defaults to **simulation mode** and will not place live trades unless explicitly configured.

## Features

- Polls a single X account every 5-10 seconds
- Uses Playwright only for ingestion (no snscrape dependency)
- Supports persistent Chrome profile so login session is reused
- Stores raw tweets in SQLite and deduplicates by tweet ID
- Rule-based parsing using regex + keyword scoring
- Basic risk controls: allowlist, max trade size, cooldown, duplicate prevention
- Broker interface with Robinhood + mock implementations
- Structured logging to console and rotating file logs
- FastAPI endpoints for health, tweets, signals, trades, pause/resume
- Async worker loop designed for long-running deployment on Railway

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
   POLL_INTERVAL_SECONDS=7
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

7. Optional: run tests:

   ```bash
   uv run pytest
   ```

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

6. Optional: run tests:

   ```bash
   pytest
   ```

---

### Seeing Live Tweets in Your Console

When a **new tweet** is detected, you will now see a log event like:

```json
{
  "message": "new_tweet_detected",
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
- Watch for:
  - `new_tweet_detected` (new tweet arrived)
  - `signal_rejected` (blocked by risk rules)
  - `trade_executed` (simulation or live execution result)

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

## API Endpoints

- `GET /health`
- `GET /tweets?limit=50`
- `GET /signals?limit=50`
- `GET /trades?limit=50`
- `POST /pause`
- `POST /resume`

## Safety Model

- `SIMULATION_MODE=true` by default.
- Live trading requires both:
  - `ENABLE_LIVE_TRADING=true`
  - `SIMULATION_MODE=false`
- Trade size is normalized and capped by `MAX_TRADE_SIZE_USD`.
- Tickers must be present in `ALLOWED_TICKERS`.

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
- Risk settings (`MAX_TRADE_SIZE_USD`, `COOLDOWN_SECONDS`, etc.)

Deploy steps:

1. Push this repo to GitHub.
2. Create a Railway service from the repo.
3. Set environment variables in Railway.
4. Deploy.

## Important Notes

- This is an MVP for controlled experimentation, not institutional-grade infrastructure.
- Start in simulation and inspect logs + DB records before enabling live mode.
- TODO: add stronger auth/session handling for Robinhood, richer parser rules, and replay/backtesting tooling.
