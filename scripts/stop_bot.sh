#!/usr/bin/env bash
# Stop the bot without removing volumes (keeps RH pickle + X profile + DB).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose stop
