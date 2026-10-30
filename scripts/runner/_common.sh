# Shared helpers for WSL uv runner scripts.
set -euo pipefail

RUNNER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${RUNNER_SCRIPT_DIR}/../../" && pwd)"
PIDFILE="${ROOT}/data/runner.pid"
LOGFILE="${ROOT}/data/logs/bot.log"
API="${BOT_API_URL:-http://127.0.0.1:8000}"

runner_powershell() {
  # Non-interactive WSL (systemd, scheduled tasks) often has no Windows directory on PATH.
  if command -v powershell.exe >/dev/null 2>&1; then
    command -v powershell.exe
    return 0
  fi
  local candidate
  for candidate in \
    /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
    /mnt/c/Windows/Sysnative/WindowsPowerShell/v1.0/powershell.exe
  do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

runner_backfill_lock_path() {
  # Same file app/ingestion/backfill_lock.py uses: next to LOG_FILE.
  local log_file dir
  log_file="$(grep -E '^LOG_FILE=' "${ROOT}/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r"')"
  log_file="${log_file:-logs/bot.log}"
  dir="$(dirname "$log_file")"
  if [[ "$dir" != /* ]]; then
    dir="${ROOT}/${dir}"
  fi
  printf '%s/backfill.lock\n' "$dir"
}

runner_start_host_chrome() {
  # WSL cannot display Chrome. Start the Windows profile the bot attaches to.
  local powershell win
  if ! powershell="$(runner_powershell)"; then
    echo "warning: powershell.exe not found; Windows Chrome was not started" >&2
    return 0
  fi
  if ! grep -qE '^PLAYWRIGHT_CDP_URL=.+' "${ROOT}/.env" 2>/dev/null; then
    return 0
  fi
  win="$(wslpath -w "${ROOT}/scripts/runner/start_chrome.ps1")"
  "$powershell" -NoProfile -ExecutionPolicy Bypass -File "$win" \
    || echo "warning: could not start Windows Chrome" >&2
}

runner_windows_host() {
  grep -m1 '^nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}' || echo "127.0.0.1"
}

runner_db_path() {
  if [[ -f "${ROOT}/data/trading_bot.db" ]]; then
    echo "${ROOT}/data/trading_bot.db"
  elif [[ -f "${ROOT}/trading_bot.db" ]]; then
    echo "${ROOT}/trading_bot.db"
  else
    echo "${ROOT}/data/trading_bot.db"
  fi
}

runner_git_branch() {
  if [[ -f "${ROOT}/.env" ]]; then
    local line
    line="$(grep -E '^RUNNER_GIT_BRANCH=' "${ROOT}/.env" | tail -1 || true)"
    if [[ -n "$line" ]]; then
      echo "${line#RUNNER_GIT_BRANCH=}"
      return
    fi
  fi
  echo "main"
}

runner_docker_bot_running() {
  docker compose ps --status running --services 2>/dev/null | grep -qx bot
}

runner_api_healthy() {
  curl -sf "${API}/health" >/dev/null 2>&1
}

runner_uv_pid() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid="$(cat "$PIDFILE")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return 0
    fi
  fi
  return 1
}

runner_ensure_dirs() {
  mkdir -p "${ROOT}/data/logs" "${ROOT}/data/x-profile" "${ROOT}/data/rh-tokens"
  touch "${ROOT}/data/trading_bot.db"
}
