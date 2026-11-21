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

echo "==> Spotify Connect (spotifyd + playerctl)"
apt-get install -y playerctl libpulse0 libdbus-1-3 curl || true
case "$(uname -m)" in
  aarch64) SP_ARCH=aarch64 ;;
  armv7l|armhf) SP_ARCH=armv7 ;;
  *) SP_ARCH="" ;;
esac
if [[ -n "$SP_ARCH" && ! -x "$APP/bin/spotifyd" ]]; then
  mkdir -p "$APP/bin"
  if curl -fsSL "https://github.com/Spotifyd/spotifyd/releases/latest/download/spotifyd-linux-$SP_ARCH-full.tar.gz" -o /tmp/spotifyd.tgz; then
    rm -rf /tmp/spotifyd-x && mkdir -p /tmp/spotifyd-x && tar -xzf /tmp/spotifyd.tgz -C /tmp/spotifyd-x
    SP_BIN="$(find /tmp/spotifyd-x -type f -name spotifyd | head -1)"   # at the top or inside a folder
    if [[ -n "$SP_BIN" ]]; then install -m 755 "$SP_BIN" "$APP/bin/spotifyd" && echo "   spotifyd พร้อมแล้ว"; fi
    chown -R "$RUN_USER:$RUN_USER" "$APP/bin"
  else
    echo "   !! ดาวน์โหลด spotifyd ไม่ได้ (ข้าม Spotify ไปก่อน รัน install.sh ใหม่ภายหลังได้)"
  fi
fi
# spotifyd 0.3.x is built against OpenSSL 1.1, Bookworm only ships OpenSSL 3 -> add libssl1.1 (sits next to 3)
if [[ -x "$APP/bin/spotifyd" ]] && ldd "$APP/bin/spotifyd" 2>/dev/null | grep -q "libssl.so.1.1 => not found"; then
  case "$(dpkg --print-architecture)" in arm64|armhf) DEB_ARCH="$(dpkg --print-architecture)" ;; *) DEB_ARCH="" ;; esac
  POOL=https://deb.debian.org/debian/pool/main/o/openssl
  F="$(curl -fsSL "$POOL/" | grep -o "libssl1\.1_[^\"]*_${DEB_ARCH}\.deb" | sort -uV | tail -1 || true)"
  if [[ -n "$DEB_ARCH" && -n "$F" ]] && curl -fsSL "$POOL/$F" -o "/tmp/$F"; then
    apt-get install -y "/tmp/$F" && echo "   libssl1.1 สำหรับ spotifyd พร้อมแล้ว"
  else
    echo "   !! ติดตั้ง libssl1.1 ไม่ได้: Spotify จะยังใช้ไม่ได้"
  fi
fi

echo "==> Bluetooth speaker (phone -> robot, any music app)"
# BlueZ + PipeWire's Bluetooth plugin receive the phone's audio (A2DP) and play it on the speaker.
apt-get install -y bluez libspa-0.2-bluetooth || true
rfkill unblock bluetooth 2>/dev/null || true
BTCONF=/etc/bluetooth/main.conf
if [[ -f $BTCONF ]]; then
  # show up as a loudspeaker; allow a phone that was "forgotten" to pair again
  grep -q '^Class *= *0x200414' $BTCONF || sed -i 's/^\[General\]/[General]\nClass = 0x200414/' $BTCONF
  grep -q '^JustWorksRepairing *= *always' $BTCONF || sed -i 's/^\[General\]/[General]\nJustWorksRepairing = always/' $BTCONF
fi
systemctl enable --now bluetooth 2>/dev/null || true
systemctl restart bluetooth 2>/dev/null || true

echo "==> config"
mkdir -p /etc/pebble
if [[ ! -f /etc/pebble/config.toml ]]; then
  cp "$SRC/config.example.toml" /etc/pebble/config.toml
  echo "   สร้าง /etc/pebble/config.toml แล้ว — อย่าลืมใส่ serial / secret"
fi
chown "$RUN_USER:$RUN_USER" /etc/pebble/config.toml
chmod 600 /etc/pebble/config.toml

echo "==> permissions for $RUN_USER (screen, input, audio, ESP32 serial, Bluetooth)"
usermod -aG video,render,input,audio,dialout,bluetooth,netdev "$RUN_USER"

echo "==> let the app set up Wi-Fi from its own screen (NetworkManager, no password prompt)"
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/50-pebble-wifi.rules <<RULES
// pebble(a): the touch app (user $RUN_USER) may scan and join Wi-Fi networks
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 && subject.user == "$RUN_USER") {
    return polkit.Result.YES;
  }
});
RULES
systemctl restart polkit 2>/dev/null || true

echo "==> serial port on GPIO14/15 (RX/TX wires to the ESP32 LED face / motors)"
# turn the UART on, and stop Linux using it as a login console (it would eat the robot's messages)
BOOT=/boot/firmware; [[ -d $BOOT ]] || BOOT=/boot
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_serial_hw 0 || true    # 0 = enable serial hardware
  raspi-config nonint do_serial_cons 1 || true  # 1 = no login shell on serial
fi
if [[ -f $BOOT/config.txt ]]; then
  grep -q '^enable_uart=1' $BOOT/config.txt || echo 'enable_uart=1' >> $BOOT/config.txt
  grep -q '^dtparam=uart0=on' $BOOT/config.txt || echo 'dtparam=uart0=on' >> $BOOT/config.txt   # Pi 5: GPIO14/15
fi
if [[ -f $BOOT/cmdline.txt ]]; then
  sed -i -E 's/ ?console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' $BOOT/cmdline.txt
fi
systemctl disable --now serial-getty@ttyAMA0.service serial-getty@serial0.service 2>/dev/null || true
echo "   ต่อสาย: Pi ขา 8 (TX) -> ESP32 GPIO44 | Pi ขา 10 (RX) <- ESP32 GPIO43 | GND -> GND  (ต้อง reboot 1 ครั้ง)"

HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"

# keeps the app running: if it ever stops or crashes, start it again (only one copy at a time)
cat > /opt/pebble/run-pebble.sh <<'RUN'
#!/bin/sh
exec 9>/tmp/pebble-app.lock
flock -n 9 || exit 0   # already running
cd /opt/pebble || exit 1
while true; do
  echo "---- start $(date)" >> "$HOME/pebble.log"
  .venv/bin/python -m pebble >> "$HOME/pebble.log" 2>&1
  echo "---- stopped with code $? $(date), restarting in 3 s" >> "$HOME/pebble.log"
  sleep 3
done
RUN
chmod 755 /opt/pebble/run-pebble.sh
if [[ "$(systemctl get-default)" == "graphical.target" ]]; then
  echo "==> desktop found: touch-screen app opens at login (Chromium kiosk)"
  apt-get install -y chromium-browser || apt-get install -y chromium || true
  systemctl disable pebble.service 2>/dev/null || true
  sudo -u "$RUN_USER" mkdir -p "$HOME_DIR/.config/autostart"
  cat > "$HOME_DIR/.config/autostart/pebble.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=pebble
Exec=/opt/pebble/run-pebble.sh
DESKTOP
  chown "$RUN_USER:$RUN_USER" "$HOME_DIR/.config/autostart/pebble.desktop"
  # desktop icon: bring the app screen back after "ตั้งค่า Wi-Fi"
  sudo -u "$RUN_USER" mkdir -p "$HOME_DIR/Desktop"
  cat > "$HOME_DIR/Desktop/pebble.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=pebble
Comment=เปิดหน้าจอแอป pebble
Icon=/opt/pebble/pebble/ui/logo-mark.png
Exec=sh -c "curl -sf http://127.0.0.1:8787/show || (sleep 4; curl -sf http://127.0.0.1:8787/show) || /opt/pebble/run-pebble.sh"
Terminal=false
DESKTOP
  chown "$RUN_USER:$RUN_USER" "$HOME_DIR/Desktop/pebble.desktop"
  chmod +x "$HOME_DIR/Desktop/pebble.desktop"
  apt-get install -y curl >/dev/null || true
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
