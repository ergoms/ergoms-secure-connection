#!/usr/bin/env bash
# Remove the Linux client installed by modes/linux/install.sh
set -euo pipefail

APP_ID="ergoms-secure-connection"
INSTALL_DIR="${ERGOMS_LINUX_PREFIX:-/opt/ergoms-secure-connection}"
LINK_DIR="/usr/local/bin"
DESKTOP_DST="/usr/share/applications/${APP_ID}.desktop"
UNIT_NAMES=("ergoms-secure-connection.service" "ergoms-vpn.service" "ops-content.service")

if [[ "$(id -u)" -ne 0 ]]; then
  src="${BASH_SOURCE[0]:-}"
  if [[ -n "$src" && -f "$src" && -r "$src" ]]; then
    exec sudo "$src" "$@"
  fi
  echo "Нужен root: sudo bash uninstall.sh" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  for UNIT_NAME in "${UNIT_NAMES[@]}"; do
    systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
    systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/$UNIT_NAME"
  done
  systemctl daemon-reload 2>/dev/null || true
fi

rm -f "$LINK_DIR/ergoms-sc" "$LINK_DIR/ergoms" "$LINK_DIR/ergoms-secure-connection"
rm -f "$DESKTOP_DST"
rm -rf "$INSTALL_DIR"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$(dirname "$DESKTOP_DST")" 2>/dev/null || true
fi

echo "Снято: $INSTALL_DIR, $LINK_DIR/ergoms-sc"
echo "Конфиг пользователя (~/.local/share/${APP_ID}) не удалялся."
