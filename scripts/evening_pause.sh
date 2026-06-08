#!/usr/bin/env bash
# Pause the worker at end of extended session (8 PM ET weekdays).
set -euo pipefail
API="${BOT_API_URL:-http://127.0.0.1:8000}"
curl -sf -X POST "${API}/pause" >/dev/null
echo "worker paused at $(date -Is)"
