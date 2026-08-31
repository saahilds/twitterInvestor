#!/usr/bin/env bash
# Install WSL crontab (backup; prefer Windows Task Scheduler on Win10).
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

MARKER="# twitterInvestor-runner"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

( crontab -l 2>/dev/null | grep -v "$MARKER" || true ) >"$TMP"

cat >>"$TMP" <<EOF
CRON_TZ=America/New_York
0 6 * * 1-5 ${ROOT}/scripts/runner/update_if_behind.sh ${MARKER}
0 7 * * 1-5 ${ROOT}/scripts/startup_backfill.sh ${MARKER}
0 20 * * 1-5 ${ROOT}/scripts/evening_pause.sh ${MARKER}
EOF

crontab "$TMP"
echo "Installed crontab entries (America/New_York):"
crontab -l | grep "$MARKER" || true
echo "On Windows 10 also run: powershell -File scripts/runner/install_windows_tasks.ps1"
