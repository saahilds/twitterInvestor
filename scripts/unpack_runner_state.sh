#!/usr/bin/env bash
# Unpack runner state tarball into data/ layout and normalize .env for WSL home runner.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 /path/to/twitterInvestor-runner-state-YYYYMMDD.tar.gz" >&2
  exit 1
fi

ARCHIVE="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "error: archive not found: $ARCHIVE" >&2
  exit 1
fi

mkdir -p data/logs data/x-profile data/rh-tokens

tar -xzf "$ARCHIVE" -C "$ROOT"

# Archive may place files at repo root or under data/ — normalize DB + sidecars.
if [[ -f "$ROOT/trading_bot.db" ]] && [[ ! -f "$ROOT/data/trading_bot.db" ]]; then
  mv "$ROOT/trading_bot.db" "$ROOT/data/trading_bot.db"
fi
if [[ -f "$ROOT/trading_bot.db-wal" ]]; then
  mv "$ROOT/trading_bot.db-wal" "$ROOT/data/trading_bot.db-wal"
fi
if [[ -f "$ROOT/trading_bot.db-shm" ]]; then
  mv "$ROOT/trading_bot.db-shm" "$ROOT/data/trading_bot.db-shm"
fi

if [[ -d "$ROOT/data/x-profile" ]] && [[ -n "$(ls -A "$ROOT/data/x-profile" 2>/dev/null || true)" ]]; then
  : # already populated
elif [[ -d "$ROOT/.playwright/x-profile" ]]; then
  mkdir -p "$ROOT/data/x-profile"
  cp -a "$ROOT/.playwright/x-profile/." "$ROOT/data/x-profile/"
fi

# Sync RH tokens into WSL home for robin_stocks.
if [[ -d "$ROOT/data/rh-tokens" ]] && [[ -n "$(ls -A "$ROOT/data/rh-tokens" 2>/dev/null || true)" ]]; then
  mkdir -p "${HOME}/.tokens"
  cp -a "$ROOT/data/rh-tokens/." "${HOME}/.tokens/"
fi

WINDOWS_HOST="$(grep -m1 '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}' || echo "127.0.0.1")"
CDP_URL="http://${WINDOWS_HOST}:9222"

if [[ -f "$ROOT/.env" ]]; then
  python3 - "$ROOT/.env" "$CDP_URL" <<'PY'
import re
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
cdp_url = sys.argv[2]
text = env_path.read_text()
updates = {
    "DATABASE_URL": "sqlite:///./data/trading_bot.db",
    "PLAYWRIGHT_USER_DATA_DIR": "data/x-profile",
    "PLAYWRIGHT_HEADLESS": "false",
    "PLAYWRIGHT_CHANNEL": "chrome",
    "PLAYWRIGHT_CDP_URL": cdp_url,
    "PLAYWRIGHT_REQUIRE_LOGIN": "false",
    "RUNNER_GIT_BRANCH": "main",
}


def set_key(body: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(body):
        return pattern.sub(line, body, count=1)
    if body and not body.endswith("\n"):
        body += "\n"
    return body + line + "\n"


for key, value in updates.items():
    text = set_key(text, key, value)
env_path.write_text(text)
PY
  echo "Updated .env for runner layout (CDP_URL=${CDP_URL})"
else
  echo "warning: no .env after unpack — copy .env.runner.example and fill secrets" >&2
fi

echo "Unpack complete. Verify: ls -la data/trading_bot.db data/x-profile data/rh-tokens"
