#!/usr/bin/env bash
# Install the bot as a systemd service (auto-start on boot, restart on crash) plus a
# watchdog timer. Run from Ubuntu (WSL) inside the repo:
#   sudo bash scripts/runner/install_service.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo: sudo bash $0" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_USER="${SUDO_USER:-}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
  echo "run via sudo from your normal user so the service runs as that user" >&2
  exit 1
fi
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
UV_BIN="$RUN_HOME/.local/bin/uv"
if [[ ! -x "$UV_BIN" ]]; then
  echo "uv not found at $UV_BIN" >&2
  exit 1
fi

if ss -ltn 2>/dev/null | grep -q ':8000 ' && ! systemctl is-active --quiet twitter-bot.service; then
  echo "port 8000 is in use: stop the bot you started by hand (Ctrl+C start_bot.sh), then re-run" >&2
  exit 1
fi

cat >/etc/systemd/system/twitter-bot.service <<EOF
[Unit]
Description=Twitter trade bot (uvicorn + worker)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$UV_BIN run uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=15
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/twitter-bot-watchdog.service <<EOF
[Unit]
Description=Health watchdog for twitter-bot.service

[Service]
Type=oneshot
ExecStart=/bin/bash $ROOT/scripts/runner/watchdog.sh
EOF

cat >/etc/systemd/system/twitter-bot-watchdog.timer <<EOF
[Unit]
Description=Run the twitter-bot watchdog every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now twitter-bot.service
systemctl enable --now twitter-bot-watchdog.timer

echo ""
echo "Installed. Useful commands:"
echo "  systemctl status twitter-bot          # is it running?"
echo "  journalctl -u twitter-bot -f          # live output"
echo "  sudo systemctl restart twitter-bot    # restart"
echo "  sudo systemctl stop twitter-bot       # stop (watchdog leaves a manual stop alone)"
echo "  journalctl -u twitter-bot-watchdog    # watchdog decisions"
