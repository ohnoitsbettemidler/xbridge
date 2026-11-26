#!/bin/bash
# XBridge installer for Raspberry Pi OS.
# Run from the folder this file is in:   sudo bash install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash install.sh"
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${XB_ROOT:-}"          # only set when testing the installer
DEST="$ROOT/opt/xbridge"
MARK_START="# --- XBridge: software I2C link to the Feather (GPIO23 = SDA pin 16, GPIO24 = SCL pin 18)"
MARK_END="# --- end XBridge"

echo "== XBridge install =="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Installing Python 3..."
  apt-get update -q && apt-get install -y -q python3
fi

echo "Copying program to $DEST"
rm -rf "$DEST.new"
mkdir -p "$DEST.new/xb"
cp "$HERE/xbridge.py" "$DEST.new/"
cp "$HERE"/xb/*.py "$HERE/xb/page.html" "$DEST.new/xb/"
rm -rf "$DEST"
mv "$DEST.new" "$DEST"
mkdir -p "$ROOT/etc/xbridge"

# Boot config: Bookworm and later keep it in /boot/firmware
CONFIG_TXT="$ROOT/boot/firmware/config.txt"
[ -f "$CONFIG_TXT" ] || CONFIG_TXT="$ROOT/boot/config.txt"
NEED_REBOOT=0
if ! grep -qF "$MARK_START" "$CONFIG_TXT"; then
  echo "Switching on the Feather link in $CONFIG_TXT"
  cp "$CONFIG_TXT" "$CONFIG_TXT.before-xbridge"
  {
    echo ""
    echo "$MARK_START"
    echo "dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=23,i2c_gpio_scl=24,i2c_gpio_delay_us=2"
    echo "gpio=23,24=pu"
    echo "$MARK_END"
  } >> "$CONFIG_TXT"
  NEED_REBOOT=1
fi

mkdir -p "$ROOT/etc/modules-load.d"
echo "i2c-dev" > "$ROOT/etc/modules-load.d/xbridge.conf"
modprobe i2c-dev 2>/dev/null || true

mkdir -p "$ROOT/etc/systemd/system"
cp "$HERE/xbridge.service" "$ROOT/etc/systemd/system/xbridge.service"
systemctl daemon-reload
systemctl enable xbridge.service >/dev/null
systemctl restart xbridge.service

HOST="$(hostname)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo "Done. Settings page:  http://$HOST.local:8080   (or http://$IP:8080)"

if [ "$NEED_REBOOT" -eq 1 ]; then
  echo ""
  echo "The Pi needs one restart to switch on the Feather link."
  echo "Restarting in 10 seconds (press Ctrl+C to do it later with: sudo reboot)"
  sleep 10
  reboot
fi
