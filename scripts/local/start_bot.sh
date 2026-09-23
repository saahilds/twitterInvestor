#!/usr/bin/env bash
# Start the bot locally (foreground uvicorn + worker).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
