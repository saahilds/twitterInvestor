#!/usr/bin/env bash
# Pull origin/main only when behind; pause, restart, resume.
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
cd "$ROOT"

LOG="${ROOT}/data/logs/update.log"
mkdir -p "${ROOT}/data/logs"
BRANCH="$(runner_git_branch)"

exec >>"$LOG" 2>&1
echo "=== update_if_behind $(date -Is) branch=${BRANCH} ==="

git fetch origin "$BRANCH" || {
  echo "git fetch failed"
  exit 1
}

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/${BRANCH}")"

if [[ "$LOCAL" == "$REMOTE" ]]; then
  echo "already up to date with origin/${BRANCH}"
  exit 0
fi

echo "behind origin/${BRANCH}: ${LOCAL} -> ${REMOTE}"

if runner_api_healthy; then
  curl -sf -X POST "${API}/pause" >/dev/null || true
  echo "worker paused"
fi

git pull --ff-only origin "$BRANCH"

"${RUNNER_SCRIPT_DIR}/stop.sh"
"${RUNNER_SCRIPT_DIR}/start.sh"

sleep 3
if runner_api_healthy; then
  curl -sf -X POST "${API}/resume" >/dev/null || true
  echo "worker resumed"
fi

echo "update complete $(date -Is)"
