#!/usr/bin/env bash
# Idempotent Raspberry Pi OS Lite installer for PiCatchUp.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo scripts/install-rpi.sh [--enable-services]" >&2
  exit 1
fi

ENABLE_SERVICES=false
ENABLE_I2C=true
for arg in "$@"; do
  case "$arg" in
    --enable-services) ENABLE_SERVICES=true ;;
    --skip-i2c) ENABLE_I2C=false ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
APP_DIR=/opt/picatchup
DATA_DIR=/var/lib/picatchup
CONFIG_DIR=/etc/picatchup
SERVICE_USER=picatchup
SYSTEM_TIMEZONE=${PICATCHUP_TIMEZONE:-Europe/Rome}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-dev build-essential ffmpeg alsa-utils \
  sqlite3 rsync openssh-client i2c-tools libjpeg-dev zlib1g-dev

for group in audio gpio i2c; do
  getent group "$group" >/dev/null || groupadd --system "$group"
done
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -a -G audio,gpio,i2c "$SERVICE_USER"

install -d -o root -g root -m 0755 "$APP_DIR"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/python" -m pip install --upgrade "$REPO_DIR[rpi]"

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
  "$DATA_DIR" "$DATA_DIR/inbox" "$DATA_DIR/audio" "$DATA_DIR/backups"
install -d -o root -g "$SERVICE_USER" -m 0750 "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/picatchup.env" ]]; then
  install -o root -g "$SERVICE_USER" -m 0640 "$REPO_DIR/.env.example" \
    "$CONFIG_DIR/picatchup.env"
  sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA_DIR|" "$CONFIG_DIR/picatchup.env"
  echo "Created $CONFIG_DIR/picatchup.env — fill GROQ_API_KEY, TEXT_MODEL and TELEGRAM_BOT_TOKEN."
else
  chown root:"$SERVICE_USER" "$CONFIG_DIR/picatchup.env"
  chmod 0640 "$CONFIG_DIR/picatchup.env"
fi
ln -sfn "$CONFIG_DIR/picatchup.env" "$DATA_DIR/.env"
chown -h "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR/.env"
ln -sfn "$APP_DIR/venv/bin/picatchup" /usr/local/bin/picatchup

if $ENABLE_I2C && command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0
fi
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl set-timezone "$SYSTEM_TIMEZONE"
fi

install -o root -g root -m 0644 "$REPO_DIR"/deploy/systemd/*.{service,timer} \
  /etc/systemd/system/
systemctl daemon-reload

run_cli() {
  runuser -u "$SERVICE_USER" -- bash -c \
    "cd '$DATA_DIR'; '$APP_DIR/venv/bin/picatchup' $*"
}
run_cli init

if ! run_cli doctor; then
  echo "Doctor reported items to fix. This is expected until secrets and hardware are connected." >&2
fi

# Upgrade already-running services without enabling anything the operator left disabled.
systemctl try-restart picatchup-device.service picatchup-bot.service || true

if $ENABLE_SERVICES; then
  systemctl enable --now picatchup-device.service picatchup-bot.service \
    picatchup-daily.timer picatchup-backup.timer
else
  echo "Services installed but not enabled. After editing $CONFIG_DIR/picatchup.env run:"
  echo "  sudo systemctl enable --now picatchup-device picatchup-bot picatchup-daily.timer picatchup-backup.timer"
fi
