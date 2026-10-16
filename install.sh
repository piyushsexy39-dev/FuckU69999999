#!/usr/bin/env bash
# One-shot installer for any Linux VPS.  Usage: sudo ./install.sh [systemd|docker]
# Default: systemd if Python >= 3.10 + systemd are available, otherwise Docker.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root: sudo ./install.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/cricket-bot
MODE="${1:-auto}"

if [ ! -f "$SRC/.env" ]; then
  cp "$SRC/.env.example" "$SRC/.env"
  echo "Created .env. Edit it (BOT_TOKEN, TELEGRAM_BOT_TOKEN, OWNER_IDS), then run ./install.sh again."
  exit 1
fi
grep -Eq '^BOT_TOKEN=.+' "$SRC/.env" || { echo "BOT_TOKEN is empty in .env"; exit 1; }
# keep both token variables in sync
TOK="$(grep -E '^BOT_TOKEN=' "$SRC/.env" | head -1 | cut -d= -f2-)"
grep -Eq '^TELEGRAM_BOT_TOKEN=.+' "$SRC/.env" || { sed -i '/^TELEGRAM_BOT_TOKEN=/d' "$SRC/.env"; echo "TELEGRAM_BOT_TOKEN=$TOK" >> "$SRC/.env"; }

pm_install() {
  if   command -v apt-get >/dev/null; then apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  elif command -v dnf     >/dev/null; then dnf install -y "$@"
  elif command -v yum     >/dev/null; then yum install -y "$@"
  elif command -v zypper  >/dev/null; then zypper -n install "$@"
  elif command -v pacman  >/dev/null; then pacman -Sy --noconfirm "$@"
  else return 1; fi
}
py_ok() { command -v python3 >/dev/null && python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)'; }

install_docker_mode() {
  command -v docker >/dev/null || { curl -fsSL https://get.docker.com | sh; }
  systemctl enable --now docker 2>/dev/null || true
  cd "$SRC"; mkdir -p data
  [ -f data/gullycric.db ] || cp -n gullycric.db* data/ 2>/dev/null || true
  if docker compose version >/dev/null 2>&1; then docker compose up -d --build
  else docker-compose up -d --build; fi
  echo "Done (Docker). Logs: docker compose logs -f"
}

install_systemd_mode() {
  if ! py_ok; then pm_install python3 python3-venv python3-pip || true; fi
  py_ok || return 1
  python3 -m venv --help >/dev/null 2>&1 || pm_install python3-venv || true
  id cricketbot >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin cricketbot
  mkdir -p "$DEST"
  if [ "$SRC" != "$DEST" ]; then cp -a "$SRC"/. "$DEST"/; fi
  cd "$DEST"
  python3 -m venv venv
  venv/bin/pip install --upgrade pip >/dev/null
  venv/bin/pip install -r requirements.txt
  chown -R cricketbot: "$DEST"; chmod 600 .env
  cp cricketbot.service /etc/systemd/system/cricketbot.service
  systemctl daemon-reload
  systemctl enable --now cricketbot
  echo "Done (systemd). Logs: journalctl -u cricketbot -f"
}

case "$MODE" in
  docker) install_docker_mode ;;
  systemd) install_systemd_mode || { echo "systemd install impossible here (need Python>=3.10)"; exit 1; } ;;
  auto)
    if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
      install_systemd_mode || { echo "Falling back to Docker..."; install_docker_mode; }
    else install_docker_mode; fi ;;
  *) echo "Usage: $0 [systemd|docker]"; exit 1 ;;
esac
