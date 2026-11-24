#!/usr/bin/env bash
# Start a Robinhood reauth (phone approval) via the bot API.
# Prefer the dashboard "Refresh RH auth" button (it has Confirm + SMS UI).
# This script auto-POSTs confirm-approval when prompt_ready flips true.
# Optional weekly cron backup when you have SSH but not the phone browser.
set -euo pipefail
API="${BOT_API_URL:-http://127.0.0.1:8000}"
# Align with robinhood_reauth_timeout_seconds default (420).
TIMEOUT_SEC="${RH_REAUTH_WAIT_SECONDS:-420}"

echo "Starting Robinhood reauth at $(date -Is)"
curl -sf -X POST "${API}/robinhood/reauth" | tee /tmp/rh_reauth_start.json
echo

confirmed=0
code_hinted=0
deadline=$((SECONDS + TIMEOUT_SEC))
while (( SECONDS < deadline )); do
  status_json="$(curl -sf "${API}/robinhood/reauth/status")"
  echo "${status_json}"
  status="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status") or "")' <<<"${status_json}")"
  prompt_ready="$(python3 -c 'import json,sys; print(1 if json.load(sys.stdin).get("prompt_ready") else 0)' <<<"${status_json}")"
  code_needed="$(python3 -c 'import json,sys; print(1 if json.load(sys.stdin).get("code_needed") else 0)' <<<"${status_json}")"
  challenge_type="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("challenge_type") or "")' <<<"${status_json}")"

  case "${status}" in
    succeeded)
      echo "Robinhood reauth succeeded at $(date -Is)"
      exit 0
      ;;
    failed)
      echo "Robinhood reauth failed at $(date -Is)" >&2
      exit 1
      ;;
  esac

  if (( prompt_ready == 1 && confirmed == 0 )); then
    echo "Prompt ready — posting confirm-approval (approve in the app first if you have not)."
    curl -sf -X POST "${API}/robinhood/reauth/confirm-approval" | tee /tmp/rh_reauth_confirm.json
    echo
    confirmed=1
  fi

  if (( code_needed == 1 && code_hinted == 0 )); then
    echo "SMS/email code needed (${challenge_type:-unknown}). Submit with:" >&2
    echo "  curl -sf -X POST ${API}/robinhood/reauth/submit-code -H 'Content-Type: application/json' -d '{\"code\":\"YOUR_CODE\"}'" >&2
    code_hinted=1
  fi

  sleep 2
done

echo "Timed out waiting for phone approval (${TIMEOUT_SEC}s)" >&2
exit 1
