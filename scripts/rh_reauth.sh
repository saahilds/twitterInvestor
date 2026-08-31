#!/usr/bin/env bash
# Start a Robinhood reauth (phone approval) via the bot API.
# Prefer the dashboard "Refresh RH auth" button while traveling.
# Optional weekly cron backup when you have SSH but not the phone browser.
set -euo pipefail
API="${BOT_API_URL:-http://127.0.0.1:8000}"
TIMEOUT_SEC="${RH_REAUTH_WAIT_SECONDS:-200}"

echo "Starting Robinhood reauth at $(date -Is)"
curl -sf -X POST "${API}/robinhood/reauth" | tee /tmp/rh_reauth_start.json
echo

deadline=$((SECONDS + TIMEOUT_SEC))
while (( SECONDS < deadline )); do
  status_json="$(curl -sf "${API}/robinhood/reauth/status")"
  status="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' <<<"${status_json}")"
  echo "${status_json}"
  case "${status}" in
    succeeded)
      echo "Robinhood reauth succeeded at $(date -Is)"
      exit 0
      ;;
    failed)
      echo "Robinhood reauth failed at $(date -Is)" >&2
      exit 1
      ;;
    awaiting_approval|idle)
      sleep 2
      ;;
    *)
      sleep 2
      ;;
  esac
done

echo "Timed out waiting for phone approval (${TIMEOUT_SEC}s)" >&2
exit 1
