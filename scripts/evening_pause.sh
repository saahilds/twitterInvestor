#!/usr/bin/env bash
# Pause the worker at end of extended session (8 PM ET weekdays) and finalize daily digest.
set -euo pipefail
API="${BOT_API_URL:-http://127.0.0.1:8000}"
curl -sf -X POST "${API}/pause" >/dev/null
echo "worker paused at $(date -Is)"
if curl -sf -X POST "${API}/digest/finalize" >/dev/null; then
  echo "daily digest finalized at $(date -Is)"
else
  echo "daily digest finalize skipped/failed at $(date -Is)" >&2
fi
