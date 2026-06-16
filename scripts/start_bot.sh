#!/usr/bin/env bash
# Start the bot container (preserves volumes; never use `down -v`).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p data/logs data/x-profile data/rh-tokens
touch data/trading_bot.db
docker compose up -d --build
