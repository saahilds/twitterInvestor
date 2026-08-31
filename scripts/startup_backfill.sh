#!/usr/bin/env bash
# Morning routine: backfill tweets since last cursor, then resume worker.
# Intended cron: Mon–Fri 7:00 AM America/New_York (see docs/VPS.md, docs/HOME_RUNNER.md).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
API="${BOT_API_URL:-http://127.0.0.1:8000}"

# shellcheck source=runner/_common.sh
source "${ROOT}/scripts/runner/_common.sh"

DB="$(runner_db_path)"

ensure_bot_running() {
  if runner_docker_bot_running; then
    return 0
  fi
  if runner_api_healthy; then
    return 0
  fi
  if runner_uv_pid >/dev/null; then
    sleep 5
    if runner_api_healthy; then
      return 0
    fi
  fi
  if command -v docker >/dev/null 2>&1 && [[ -f "${ROOT}/docker-compose.yml" ]]; then
    if ! runner_docker_bot_running; then
      echo "docker bot not running; starting..."
      "${ROOT}/scripts/start_bot.sh"
      sleep 15
      return 0
    fi
  fi
  echo "uv runner not running; starting..."
  "${ROOT}/scripts/runner/start.sh"
  sleep 15
}

ensure_bot_running

SINCE="$(python3 - "$DB" <<'PY'
import sys
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

db_path = Path(sys.argv[1])
et = ZoneInfo("America/New_York")
now_et = datetime.now(et)


def previous_weekday(date_et: datetime):
    day = date_et.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def parse_posted_at(raw: str) -> datetime:
    text = raw.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if db_path.is_file():
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT max(posted_at) FROM tweets").fetchone()
    finally:
        conn.close()
    if row and row[0]:
        since = parse_posted_at(str(row[0])) - timedelta(hours=1)
        print(since.strftime("%Y-%m-%dT%H:%M:%S+00:00"))
        sys.exit(0)

day = previous_weekday(now_et)
session_end = datetime.combine(day, time(20, 0), tzinfo=et)
print(session_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"))
PY
)"

echo "backfill since ${SINCE}"

if runner_docker_bot_running; then
  docker compose exec -T bot uv run python -m app.cli.backfill --since "${SINCE}"
else
  uv run python -m app.cli.backfill --since "${SINCE}"
fi

curl -sf -X POST "${API}/resume" >/dev/null
echo "worker resumed at $(date -Is)"
