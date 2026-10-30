#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
cd "$ROOT"

if runner_uv_pid >/dev/null; then
  echo "runner already running (pid $(cat "$PIDFILE"))"
  exit 0
fi

runner_ensure_dirs
uv sync

CDP_HOST="$(runner_windows_host)"
if grep -q '^PLAYWRIGHT_CDP_URL=' "$ROOT/.env" 2>/dev/null; then
  # Prefer URL from .env; fall back to relay port 9223 then Chrome 9222.
  CDP_URL="$(grep -E '^PLAYWRIGHT_CDP_URL=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '\r')"
  if [[ -z "$CDP_URL" ]]; then
    CDP_URL="http://${CDP_HOST}:9223"
  fi
  if curl -sf "$CDP_URL/json/version" >/dev/null 2>&1; then
    echo "Chrome CDP reachable at $CDP_URL"
  elif curl -sf "http://${CDP_HOST}:9223/json/version" >/dev/null 2>&1; then
    echo "Chrome CDP relay reachable at http://${CDP_HOST}:9223"
  else
    echo "warning: Chrome CDP not reachable (${CDP_URL}) — run start_chrome.ps1 on Windows" >&2
  fi
fi

nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 >>"$LOGFILE" 2>&1 &
echo $! >"$PIDFILE"
sleep 2

if runner_uv_pid >/dev/null; then
  echo "runner started pid $(cat "$PIDFILE") log=$LOGFILE"
else
  echo "runner failed to start — see $LOGFILE" >&2
  exit 1
fi
