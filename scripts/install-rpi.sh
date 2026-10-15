#!/usr/bin/env bash
# Install CamServer on a headless Raspberry Pi (Debian/Raspberry Pi OS).
# Run as root: sudo ./scripts/install-rpi.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

APP_USER="camserver"
APP_DIR="/opt/camserver"
DATA_DIR="/var/lib/camserver"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing ffmpeg (if missing)"
if ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ffmpeg
fi

echo "==> Installing uv (if missing)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Creating system user '${APP_USER}'"
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> Copying application to ${APP_DIR}"
mkdir -p "${APP_DIR}"
rm -rf "${APP_DIR}"/.venv "${APP_DIR}"/data 2>/dev/null || true
cp -r "${SRC_DIR}/." "${APP_DIR}/"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> Creating data directory ${DATA_DIR}"
install -d -o "${APP_USER}" -g "${APP_USER}" -m 700 "${DATA_DIR}"

echo "==> Installing dependencies"
sudo -u "${APP_USER}" bash -c "cd ${APP_DIR} && uv sync --locked"

echo "==> Installing systemd service"
cp "${APP_DIR}/scripts/camserver.service" /etc/systemd/system/camserver.service
systemctl daemon-reload
systemctl enable camserver

echo "==> Done."
echo "Start it with:  sudo systemctl start camserver"
echo "Then open:      http://<raspberry-pi-ip>:8000"
