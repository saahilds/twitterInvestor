#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
cd "$ROOT"

if runner_api_healthy; then
  curl -sf -X POST "${API}/pause" >/dev/null || true
  echo "worker paused via API"
fi

if runner_uv_pid >/dev/null; then
  pid="$(runner_uv_pid)"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  rm -f "$PIDFILE"
  echo "runner stopped pid $pid"
else
  echo "runner not running"
  rm -f "$PIDFILE"
fi
