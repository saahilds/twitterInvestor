# Twitter/X Signal Trader (Phase 1 MVP)

Minimal, reliability-first trading bot that watches one Twitter/X account, parses rule-based trade signals, applies basic risk checks, and places very small Robinhood orders.

> Safety first: the bot defaults to **simulation mode** and will not place live trades unless explicitly configured.

## Features

- Polls a single X account every 5-10 seconds
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

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Configure environment:

   ```bash
   cp .env.example .env
   ```

3. Run app:

   ```bash
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. Run tests:

   ```bash
   uv run pytest
   ```

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
