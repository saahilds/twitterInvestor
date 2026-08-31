# Shared helpers for WSL uv runner scripts.
set -euo pipefail

RUNNER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${RUNNER_SCRIPT_DIR}/../../" && pwd)"
PIDFILE="${ROOT}/data/runner.pid"
LOGFILE="${ROOT}/data/logs/bot.log"
API="${BOT_API_URL:-http://127.0.0.1:8000}"

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
