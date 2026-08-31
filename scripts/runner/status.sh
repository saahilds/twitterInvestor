#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
cd "$ROOT"

if runner_uv_pid >/dev/null; then
  echo "runner pid $(cat "$PIDFILE") (running)"
else
  echo "runner not running"
fi

if runner_api_healthy; then
  curl -sf "${API}/health" | python3 -m json.tool 2>/dev/null || curl -sf "${API}/health"
else
  echo "API not reachable at ${API}"
fi
