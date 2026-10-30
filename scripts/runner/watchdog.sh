#!/usr/bin/env bash
# Run by twitter-bot-watchdog.timer (as root) every 2 minutes.
# Restarts twitter-bot.service when the API stops answering or the X feed has been
# failing for a long time, and pushes a phone alert for crashes and restarts.
set -uo pipefail

SERVICE="twitter-bot.service"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="/var/lib/twitter-bot-watchdog"
API_URL="http://127.0.0.1:8000/health"
STARTUP_GRACE_SECONDS="${STARTUP_GRACE_SECONDS:-600}"
MAX_API_FAILURES="${MAX_API_FAILURES:-3}"
FEED_FAILURES_BEFORE_RESTART="${FEED_FAILURES_BEFORE_RESTART:-20}"
MAX_FEED_RESTARTS="${MAX_FEED_RESTARTS:-3}"
MIN_SECONDS_BETWEEN_RESTARTS="${MIN_SECONDS_BETWEEN_RESTARTS:-900}"

mkdir -p "$STATE_DIR"

env_value() {
  grep -E "^$1=" "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r'
}

notify() {
  local title="$1" message="$2" url
  url="$(env_value ALERT_WEBHOOK_URL)"
  [[ -z "$url" ]] && return 0
  if [[ "$url" == *ntfy* ]]; then
    curl -fsS -m 15 -H "Title: $title" -H "Priority: 4" -H "Tags: robot" -d "$message" "$url" >/dev/null || true
  else
    local body
    body="$(python3 -c 'import json,sys; t=f"[{sys.argv[1]}] {sys.argv[2]}"; print(json.dumps({"text": t, "content": t}))' "$title" "$message")"
    curl -fsS -m 15 -H "Content-Type: application/json" -d "$body" "$url" >/dev/null || true
  fi
}

read_state() { cat "$STATE_DIR/$1" 2>/dev/null || echo "$2"; }
write_state() { echo "$2" >"$STATE_DIR/$1"; }

restart_bot() {
  local reason="$1" now last
  now="$(date +%s)"
  last="$(read_state last_restart 0)"
  if (( now - last < MIN_SECONDS_BETWEEN_RESTARTS )); then
    echo "skip restart (last restart $((now - last))s ago): $reason"
    return
  fi
  write_state last_restart "$now"
  write_state api_failures 0
  echo "restarting $SERVICE: $reason"
  systemctl restart "$SERVICE"
  notify "Trading bot: bot_restarted" "Watchdog restarted the bot: $reason"
}

systemctl is-enabled --quiet "$SERVICE" || exit 0
if systemctl is-failed --quiet "$SERVICE"; then
  systemctl reset-failed "$SERVICE"
  restart_bot "service had failed"
  exit 0
fi
# Stopped on purpose (systemctl stop): leave it alone.
systemctl is-active --quiet "$SERVICE" || exit 0

# systemd restarts crashed processes itself (Restart=always); tell the user it happened.
restarts="$(systemctl show -p NRestarts --value "$SERVICE")"
seen_restarts="$(read_state seen_restarts "$restarts")"
write_state seen_restarts "$restarts"
if (( restarts > seen_restarts )); then
  notify "Trading bot: bot_restarted" "Bot crashed and systemd restarted it ($((restarts - seen_restarts))x since last check)."
fi

# Startup blocks on Robinhood login (may wait for phone approval); give it time.
started_at="$(systemctl show -p ActiveEnterTimestampMonotonic --value "$SERVICE")"
uptime_us="$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)"
if (( (uptime_us - started_at) / 1000000 < STARTUP_GRACE_SECONDS )); then
  exit 0
fi

health="$(curl -fsS -m 20 "$API_URL" 2>/dev/null)"
if [[ -z "$health" ]]; then
  failures=$(( $(read_state api_failures 0) + 1 ))
  write_state api_failures "$failures"
  echo "health check failed ($failures/$MAX_API_FAILURES)"
  if (( failures >= MAX_API_FAILURES )); then
    restart_bot "API not responding for $failures checks"
  fi
  exit 0
fi
write_state api_failures 0

feed_failures="$(python3 -c 'import json,sys; print(int(json.loads(sys.argv[1]).get("x_feed_consecutive_failures") or 0))' "$health" 2>/dev/null || echo 0)"
if (( feed_failures == 0 )); then
  write_state feed_restarts 0
  exit 0
fi
if (( feed_failures >= FEED_FAILURES_BEFORE_RESTART )); then
  feed_restarts="$(read_state feed_restarts 0)"
  if (( feed_restarts >= MAX_FEED_RESTARTS )); then
    # Restarting has not helped (e.g. X logged out); stop churning and ask for a human once.
    if [[ "$(read_state feed_gave_up 0)" != "1" ]]; then
      write_state feed_gave_up 1
      notify "Trading bot: x_feed_down" "X feed still failing after $feed_restarts watchdog restarts. Needs manual action: check X login in the bot's Chrome window."
    fi
    exit 0
  fi
  write_state feed_gave_up 0
  write_state feed_restarts $(( feed_restarts + 1 ))
  # A fresh process re-reads .env (e.g. a new Chrome CDP address) and rebuilds the browser link.
  restart_bot "X feed failed $feed_failures polls in a row"
fi
