#!/usr/bin/env bash
# First boot on WSL after clone + optional state unpack.
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
cd "$ROOT"

if [[ $# -ge 1 ]]; then
  "${ROOT}/scripts/unpack_runner_state.sh" "$1"
fi

runner_ensure_dirs
uv sync
uv run playwright install chromium

echo ""
echo "Next steps (Windows):"
echo "  1. powershell -ExecutionPolicy Bypass -File scripts/runner/start_chrome.ps1"
echo "  2. powershell -ExecutionPolicy Bypass -File scripts/runner/install_windows_tasks.ps1"
echo "  3. Install Tailscale on Windows + your phone"
echo "  4. ${RUNNER_SCRIPT_DIR}/start.sh"
echo "See docs/HOME_RUNNER.md"
