#!/usr/bin/env bash
# Remove systemd unit installed by modes/linux/install-service.sh
set -euo pipefail

UNIT_NAMES=("ergoms-vpn.service" "ops-content.service")

if [[ "${EUID:-}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo "$0" "$@"
  fi
  echo "Нужен root: sudo $0" >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  for UNIT_NAME in "${UNIT_NAMES[@]}"; do
    systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
    systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/$UNIT_NAME"
    echo "Removed $UNIT_NAME"
  done
  systemctl daemon-reload 2>/dev/null || true
fi

# Leftover from the old SSH-SOCKS user unit
REAL_USER="${SUDO_USER:-${USER:-}}"
if [[ -n "$REAL_USER" && "$REAL_USER" != "root" ]]; then
  HOME_DIR="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)"
  if [[ -n "$HOME_DIR" ]]; then
    systemctl --user --machine="${REAL_USER}@" disable --now ops-content-socks.service 2>/dev/null || true
    rm -f "$HOME_DIR/.config/systemd/user/ops-content-socks.service"
  fi
fi
