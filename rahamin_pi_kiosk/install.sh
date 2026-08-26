#!/bin/sh
set -eu

KIOSK_USER="${KIOSK_USER:-rahamin}"
PROFILE_SOURCE="${1:-}"
PROFILE_TARGET="/etc/openvpn/client/baiamonte-kiosk.conf"
INSTALL_ROOT="/usr/local/lib/baiamonte-tv-kiosk"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  echo "Kiosk user '$KIOSK_USER' does not exist." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends openvpn chromium cage curl ca-certificates

install -d -m 0755 /etc/openvpn/client

if [ -n "$PROFILE_SOURCE" ]; then
  if [ ! -f "$PROFILE_SOURCE" ]; then
    echo "CloudConnexa profile not found: $PROFILE_SOURCE" >&2
    exit 1
  fi
  if grep -Eiq '^[[:space:]]*(up|down|route-up|ipchange|client-connect|client-disconnect|learn-address|auth-user-pass-verify|tls-verify|plugin|script-security)[[:space:]]' "$PROFILE_SOURCE"; then
    echo "Refusing a profile containing executable OpenVPN directives." >&2
    exit 1
  fi
  install -m 0600 -o root -g root "$PROFILE_SOURCE" "$PROFILE_TARGET"
elif [ ! -s "$PROFILE_TARGET" ]; then
  echo "Supply the dedicated CloudConnexa .ovpn profile on first install." >&2
  exit 1
fi

install -d -m 0755 "$INSTALL_ROOT"
install -m 0755 "$SCRIPT_DIR/baiamonte-kiosk-browser" "$INSTALL_ROOT/baiamonte-kiosk-browser"
install -m 0755 "$SCRIPT_DIR/baiamonte-kiosk-wait" "$INSTALL_ROOT/baiamonte-kiosk-wait"
install -m 0755 "$SCRIPT_DIR/baiamonte-kiosk-network" "$INSTALL_ROOT/baiamonte-kiosk-network"
install -m 0644 "$SCRIPT_DIR/baiamonte-kiosk-network.service" /etc/systemd/system/baiamonte-kiosk-network.service
install -m 0644 "$SCRIPT_DIR/baiamonte-tv-kiosk.service" /etc/systemd/system/baiamonte-tv-kiosk.service
install -d -m 0755 /etc/chromium/policies/managed
install -m 0644 "$SCRIPT_DIR/chromium-baiamonte-policy.json" /etc/chromium/policies/managed/baiamonte-kiosk.json

install -d -m 0755 /etc/systemd/system/openvpn-client@baiamonte-kiosk.service.d
install -m 0644 "$SCRIPT_DIR/openvpn-restart.conf" /etc/systemd/system/openvpn-client@baiamonte-kiosk.service.d/restart.conf

usermod -a -G video,render,input "$KIOSK_USER"
install -d -m 0700 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/chromium-baiamonte-kiosk"

systemctl daemon-reload
systemctl disable openvpn-client@baiamonte-kiosk.service >/dev/null 2>&1 || true
systemctl enable baiamonte-kiosk-network.service
systemctl restart baiamonte-kiosk-network.service
systemctl enable baiamonte-tv-kiosk.service
systemctl restart baiamonte-tv-kiosk.service

echo "Rahamin Pi Baiamonte kiosk installed."
echo "Network selector: systemctl status baiamonte-kiosk-network.service"
echo "VPN: systemctl status openvpn-client@baiamonte-kiosk.service"
echo "Kiosk: systemctl status baiamonte-tv-kiosk.service"
