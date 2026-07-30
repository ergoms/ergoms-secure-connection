#!/usr/bin/env bash
# Install systemd --user unit for SOCKS tunnel.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_SRC="$ROOT/modes/socks/ops-content-socks.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/ops-content-socks.service"
RUN="$ROOT/modes/socks/run-tunnel.sh"

chmod +x "$RUN" "$ROOT/modes/socks/install-service.sh" "$ROOT/modes/socks/uninstall-service.sh" 2>/dev/null || true

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found — this installer needs systemd" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"
# Escape for sed
esc_root="$(printf '%s' "$ROOT" | sed 's/[\/&]/\\&/g')"
sed "s|/REPLACE/ops-content|$esc_root|g" "$UNIT_SRC" >"$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable ops-content-socks.service
systemctl --user restart ops-content-socks.service

# Allow user services after logout (optional but useful on servers/desktops)
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" 2>/dev/null || true
fi

echo "Installed: $UNIT_DST"
echo "Status:    systemctl --user status ops-content-socks"
echo "Logs:      journalctl --user -u ops-content-socks -f"
echo
echo "Client proxy (full internet) — add to ~/.bashrc or use:"
echo "  source $ROOT/var/cli.env"
echo "Or: ./ops-content.sh on   # writes env + gsettings when SOCKS_SCOPE=full"
