#!/usr/bin/env bash
# One-time server setup (Ubuntu 22.04/24.04). Run from the project folder: bash deploy/setup-server.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi
sudo timedatectl set-timezone "${DESK_TZ:-America/New_York}"

if [ ! -f .env ]; then  # fresh random API secrets, never leave the server
  cat > .env <<EOF
FREQTRADE__API_SERVER__USERNAME=desk
FREQTRADE__API_SERVER__PASSWORD=$(openssl rand -hex 16)
FREQTRADE__API_SERVER__JWT_SECRET_KEY=$(openssl rand -hex 32)
FREQTRADE__API_SERVER__WS_TOKEN=$(openssl rand -hex 16)
EOF
  chmod 600 .env
fi

mkdir -p state reports user_data/logs
sudo chown -R 1000:1000 state reports user_data desk  # container user ftuser = uid 1000
sudo docker compose pull
sudo docker compose up -d
sleep 20
sudo docker compose ps
