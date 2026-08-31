#!/usr/bin/env bash
# Pack DB, .env, X profile, and RH tokens for one-time migration to the home runner.
# Run on the Mac (or current live machine) after pausing/stopping the bot.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="$(date +%Y%m%d)"
OUT="${1:-${HOME}/Desktop/twitterInvestor-runner-state-${STAMP}.tar.gz}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/data"

if [[ -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env" "$STAGE/.env"
else
  echo "warning: no .env in repo root" >&2
fi

pack_db() {
  local src="$1"
  cp "$src" "$STAGE/data/trading_bot.db"
  if [[ -f "${src}-wal" ]]; then
    cp "${src}-wal" "$STAGE/data/trading_bot.db-wal"
  fi
  if [[ -f "${src}-shm" ]]; then
    cp "${src}-shm" "$STAGE/data/trading_bot.db-shm"
  fi
}

if [[ -f "$ROOT/data/trading_bot.db" ]]; then
  pack_db "$ROOT/data/trading_bot.db"
elif [[ -f "$ROOT/trading_bot.db" ]]; then
  pack_db "$ROOT/trading_bot.db"
else
  echo "error: no trading_bot.db found (repo root or data/)" >&2
  exit 1
fi

if [[ -d "$ROOT/data/x-profile" ]]; then
  mkdir -p "$STAGE/data/x-profile"
  cp -a "$ROOT/data/x-profile/." "$STAGE/data/x-profile/"
elif [[ -d "$ROOT/.playwright/x-profile" ]]; then
  mkdir -p "$STAGE/data/x-profile"
  cp -a "$ROOT/.playwright/x-profile/." "$STAGE/data/x-profile/"
else
  echo "warning: no X profile directory found" >&2
fi

if [[ -d "$ROOT/data/rh-tokens" ]] && [[ -n "$(ls -A "$ROOT/data/rh-tokens" 2>/dev/null || true)" ]]; then
  mkdir -p "$STAGE/data/rh-tokens"
  cp -a "$ROOT/data/rh-tokens/." "$STAGE/data/rh-tokens/"
elif [[ -d "${HOME}/.tokens" ]] && [[ -n "$(ls -A "${HOME}/.tokens" 2>/dev/null || true)" ]]; then
  mkdir -p "$STAGE/data/rh-tokens"
  cp -a "${HOME}/.tokens/." "$STAGE/data/rh-tokens/"
else
  echo "warning: no Robinhood token dir found" >&2
fi

tar -czf "$OUT" -C "$STAGE" .
echo "Created runner state archive: $OUT"
echo "Transfer privately (USB / Drive). Do not commit or email."
