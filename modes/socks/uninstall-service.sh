#!/usr/bin/env bash
set -euo pipefail

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/ops-content-socks.service"

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now ops-content-socks.service 2>/dev/null || true
  systemctl --user daemon-reload 2>/dev/null || true
fi
rm -f "$UNIT_DST"
echo "Removed ops-content-socks user service"
