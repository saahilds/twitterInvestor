#!/usr/bin/env bash
# Morning routine: backfill tweets since last cursor, then resume worker.
# Intended cron: Mon–Fri 7:00 AM America/New_York (see docs/VPS.md).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DB="${ROOT}/data/trading_bot.db"
API="${BOT_API_URL:-http://127.0.0.1:8000}"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx bot; then
  echo "bot container not running; starting..."
  "${ROOT}/scripts/start_bot.sh"
  sleep 15
fi

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
docker compose exec -T bot uv run python -m app.cli.backfill --since "${SINCE}"

curl -sf -X POST "${API}/resume" >/dev/null
echo "worker resumed at $(date -Is)"
