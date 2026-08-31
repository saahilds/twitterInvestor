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
  if curl -sf "http://${CDP_HOST}:9222/json/version" >/dev/null 2>&1; then
    echo "Chrome CDP reachable at http://${CDP_HOST}:9222"
  else
    echo "warning: Chrome CDP not reachable at http://${CDP_HOST}:9222 — run start_chrome.ps1 on Windows" >&2
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
