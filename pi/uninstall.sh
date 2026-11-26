#!/bin/bash
# Removes XBridge from the Pi:   sudo bash uninstall.sh
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash uninstall.sh"
  exit 1
fi

systemctl disable --now xbridge.service 2>/dev/null || true
rm -f /etc/systemd/system/xbridge.service /etc/modules-load.d/xbridge.conf
systemctl daemon-reload
rm -rf /opt/xbridge

CONFIG_TXT=/boot/firmware/config.txt
[ -f "$CONFIG_TXT" ] || CONFIG_TXT=/boot/config.txt
sed -i '/^# --- XBridge: software I2C link/,/^# --- end XBridge/d' "$CONFIG_TXT"

echo "XBridge removed. Your settings are still in /etc/xbridge (delete that folder too if you want)."
echo "Restart the Pi to finish:  sudo reboot"
