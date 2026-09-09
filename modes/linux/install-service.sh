#!/usr/bin/env bash
# Install systemd system unit for ERGOMS VPN (autostart).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_SRC="$ROOT/modes/linux/ergoms-vpn.service"
UNIT_NAME="ergoms-vpn.service"
UNIT_DST="/etc/systemd/system/$UNIT_NAME"
LEGACY_UNIT="ops-content.service"

find_python() {
  local c
  if [[ -n "${ERGOMS_VPN_PYTHON:-}" && -x "${ERGOMS_VPN_PYTHON}" ]]; then
    echo "$ERGOMS_VPN_PYTHON"
    return 0
  fi
  if [[ -n "${OPS_CONTENT_PYTHON:-}" && -x "${OPS_CONTENT_PYTHON}" ]]; then
    echo "$OPS_CONTENT_PYTHON"
    return 0
  fi
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
    return 0
  fi
  if [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
    echo "$ROOT/.venv/Scripts/python.exe"
    return 0
  fi
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c "import sys" 2>/dev/null; then
        command -v "$c"
        return 0
      fi
    fi
  done
  echo "Python 3 not found" >&2
  exit 1
}

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found — нужен systemd" >&2
  exit 1
fi

if [[ "${EUID:-}" -ne 0 ]]; then
  PY="$(find_python)"
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Нужен root: sudo $0" >&2
    exit 1
  fi
  exec sudo --preserve-env=PATH env ERGOMS_VPN_PYTHON="$PY" "$0" "$@"
fi

if [[ ! -f "$ROOT/config.json" ]]; then
  echo "Нет $ROOT/config.json — сначала: ./ergoms-vpn.sh init" >&2
  exit 1
fi

REAL_USER="${SUDO_USER:-${USER:-root}}"
if [[ "$REAL_USER" == "root" && -n "${SUDO_USER:-}" ]]; then
  REAL_USER="$SUDO_USER"
fi
REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)"
REAL_HOME="${REAL_HOME:-${HOME:-/root}}"
PY="$(find_python)"
PY="$(readlink -f "$PY" 2>/dev/null || echo "$PY")"

# Drop leftover user-unit from the old SSH-SOCKS installer
remove_legacy_user_unit() {
  local user="$1"
  local home dst
  home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)"
  [[ -z "$home" ]] && return 0
  dst="$home/.config/systemd/user/ops-content-socks.service"
  systemctl --user --machine="${user}@" disable --now ops-content-socks.service 2>/dev/null || true
  sudo -u "$user" systemctl --user disable --now ops-content-socks.service 2>/dev/null || true
  rm -f "$dst"
}

remove_legacy_user_unit "$REAL_USER"

if systemctl list-unit-files "$LEGACY_UNIT" >/dev/null 2>&1; then
  systemctl disable --now "$LEGACY_UNIT" 2>/dev/null || true
  rm -f "/etc/systemd/system/$LEGACY_UNIT"
fi

# Avoid two clients fighting for :1080 / TUN
if [[ -x "$ROOT/ergoms-vpn.sh" ]]; then
  sudo -u "$REAL_USER" env HOME="$REAL_HOME" "$ROOT/ergoms-vpn.sh" off >/dev/null 2>&1 || true
  "$ROOT/ergoms-vpn.sh" off >/dev/null 2>&1 || true
elif [[ -x "$ROOT/ops-content.sh" ]]; then
  sudo -u "$REAL_USER" env HOME="$REAL_HOME" "$ROOT/ops-content.sh" off >/dev/null 2>&1 || true
  "$ROOT/ops-content.sh" off >/dev/null 2>&1 || true
fi

esc() { printf '%s' "$1" | sed 's/[\/&]/\\&/g'; }

sed \
  -e "s|/REPLACE/root|$(esc "$ROOT")|g" \
  -e "s|/REPLACE/python|$(esc "$PY")|g" \
  -e "s|/REPLACE/home|$(esc "$REAL_HOME")|g" \
  -e "s|REPLACE_USER|$(esc "$REAL_USER")|g" \
  "$UNIT_SRC" >"$UNIT_DST"
chmod 644 "$UNIT_DST"

systemctl daemon-reload
systemctl enable --now "$UNIT_NAME"

echo "Installed: $UNIT_DST"
echo "Start:     systemctl enable --now ergoms-vpn"
echo "Status:    systemctl status ergoms-vpn"
echo "Logs:      journalctl -u ergoms-vpn -f"
echo "Remove:    ./ergoms-vpn.sh uninstall-service"
echo
systemctl --no-pager --full status ergoms-vpn.service || true
