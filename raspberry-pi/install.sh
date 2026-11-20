#!/usr/bin/env bash
# Installs pebble(a) on a Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit).
# Usage:  cd pebble-firmware/raspberry-pi && sudo ./install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "ใช้ sudo ./install.sh"; exit 1; fi
RUN_USER="${SUDO_USER:-pi}"
SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/pebble

echo "==> apt packages"
apt-get update
apt-get install -y python3-venv python3-pip python3-opencv python3-picamera2 \
  ffmpeg libportaudio2 libsdl2-2.0-0 fonts-noto-core fonts-noto-color-emoji fonts-thai-tlwg bluez

echo "==> copy app to $APP"
mkdir -p "$APP"
cp -r "$SRC/pebble" "$SRC/requirements.txt" "$APP/"
chown -R "$RUN_USER:$RUN_USER" "$APP"

echo "==> python packages"
sudo -u "$RUN_USER" python3 -m venv --system-site-packages "$APP/.venv"
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" install --upgrade pip
sudo -u "$RUN_USER" "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"

echo "==> config"
mkdir -p /etc/pebble
if [[ ! -f /etc/pebble/config.toml ]]; then
  cp "$SRC/config.example.toml" /etc/pebble/config.toml
  echo "   สร้าง /etc/pebble/config.toml แล้ว — อย่าลืมใส่ serial / secret"
fi
chown "$RUN_USER:$RUN_USER" /etc/pebble/config.toml
chmod 600 /etc/pebble/config.toml

echo "==> permissions for $RUN_USER (screen, input, audio, ESP32 serial, Bluetooth)"
usermod -aG video,render,input,audio,dialout,bluetooth "$RUN_USER"

HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ "$(systemctl get-default)" == "graphical.target" ]]; then
  echo "==> desktop found: touch-screen app opens at login (Chromium kiosk)"
  apt-get install -y chromium-browser || apt-get install -y chromium || true
  systemctl disable pebble.service 2>/dev/null || true
  sudo -u "$RUN_USER" mkdir -p "$HOME_DIR/.config/autostart"
  cat > "$HOME_DIR/.config/autostart/pebble.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=pebble
Exec=sh -c "cd /opt/pebble && exec .venv/bin/python -m pebble >> \$HOME/pebble.log 2>&1"
DESKTOP
  chown "$RUN_USER:$RUN_USER" "$HOME_DIR/.config/autostart/pebble.desktop"
  START_HINT="sudo reboot   (แอปจะเปิดเองหลังล็อกอิน · log: ~/pebble.log)"
else
  echo "==> no desktop (Lite): robot face via systemd"
  sed "s/__USER__/$RUN_USER/" "$SRC/pebble.service" > /etc/systemd/system/pebble.service
  systemctl daemon-reload
  systemctl enable pebble.service
  START_HINT="sudo systemctl start pebble   (log: journalctl -u pebble -f)"
fi

cat <<MSG

เสร็จแล้ว ขั้นต่อไป:
  1) sudo nano /etc/pebble/config.toml   (ใส่ server.url, robot.serial, robot.secret, thingsboard.token)
  2) $START_HINT
MSG
