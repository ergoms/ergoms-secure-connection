#!/usr/bin/env bash
# Install systemd system unit for ERGOMS SECURE CONNECTION (autostart).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_NAME="ergoms-secure-connection.service"
UNIT_DST="/etc/systemd/system/$UNIT_NAME"
LEGACY_UNITS=("ergoms-vpn.service" "ops-content.service")
APP_EXE="ERGOMS SECURE CONNECTION"

find_python() {
  local c
  if [[ -n "${ERGOMS_SC_PYTHON:-}" && -x "${ERGOMS_SC_PYTHON}" ]]; then
    echo "$ERGOMS_SC_PYTHON"
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

find_frozen_exe() {
  local cand
  if [[ -n "${ERGOMS_SC_EXE:-}" && -x "${ERGOMS_SC_EXE}" ]]; then
    echo "$ERGOMS_SC_EXE"
    return 0
  fi
  # Repo checkout with sources always uses Python, even if dist/ exists.
  if [[ -f "$ROOT/desktop/__main__.py" ]]; then
    return 1
  fi
  for cand in "$ROOT/$APP_EXE" "$ROOT/ergoms-secure-connection"; do
    if [[ -x "$cand" && ! -d "$cand" ]]; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found — нужен systemd" >&2
  exit 1
fi

FROZEN_EXE="$(find_frozen_exe || true)"

if [[ "${EUID:-}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Нужен root: sudo $0" >&2
    exit 1
  fi
  if [[ -n "$FROZEN_EXE" ]]; then
    exec sudo --preserve-env=PATH env ERGOMS_SC_EXE="$FROZEN_EXE" "$0" "$@"
  fi
  PY="$(find_python)"
  exec sudo --preserve-env=PATH env ERGOMS_SC_PYTHON="$PY" "$0" "$@"
fi

REAL_USER="${SUDO_USER:-${USER:-root}}"
if [[ "$REAL_USER" == "root" && -n "${SUDO_USER:-}" ]]; then
  REAL_USER="$SUDO_USER"
fi
REAL_HOME="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6 || true)"
REAL_HOME="${REAL_HOME:-${HOME:-/root}}"
XDG_DATA="${REAL_HOME}/.local/share/ergoms-secure-connection"

if [[ -z "$FROZEN_EXE" ]]; then
  FROZEN_EXE="$(find_frozen_exe || true)"
fi

if [[ -n "$FROZEN_EXE" ]]; then
  FROZEN_EXE="$(readlink -f "$FROZEN_EXE" 2>/dev/null || echo "$FROZEN_EXE")"
  DATA_DIR="$XDG_DATA"
  WORK_DIR="$(cd "$(dirname "$FROZEN_EXE")" && pwd)"
  if [[ ! -f "$DATA_DIR/config.json" ]]; then
    echo "Нет $DATA_DIR/config.json — сначала: ergoms-sc  (Настройки → Из файла) или ergoms-sc init" >&2
    exit 1
  fi
  systemd_escape() { printf '%s' "$1" | sed 's/ /\\ /g'; }
  EXEC_START="$(systemd_escape "$FROZEN_EXE") watch"
  EXEC_STOP="$(systemd_escape "$FROZEN_EXE") off"
  "$FROZEN_EXE" off >/dev/null 2>&1 || true
else
  if [[ ! -f "$ROOT/config.json" ]]; then
    echo "Нет $ROOT/config.json — сначала: ./ergoms-secure-connection.sh init" >&2
    exit 1
  fi
  PY="$(find_python)"
  PY="$(readlink -f "$PY" 2>/dev/null || echo "$PY")"
  DATA_DIR="$ROOT"
  WORK_DIR="$ROOT"
  EXEC_START="$PY -m desktop watch"
  EXEC_STOP="$PY -m desktop off"
  if [[ -x "$ROOT/ergoms-secure-connection.sh" ]]; then
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" "$ROOT/ergoms-secure-connection.sh" off >/dev/null 2>&1 || true
    "$ROOT/ergoms-secure-connection.sh" off >/dev/null 2>&1 || true
  fi
fi

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

for LEGACY_UNIT in "${LEGACY_UNITS[@]}"; do
  if systemctl list-unit-files "$LEGACY_UNIT" >/dev/null 2>&1; then
    systemctl disable --now "$LEGACY_UNIT" 2>/dev/null || true
    rm -f "/etc/systemd/system/$LEGACY_UNIT"
  fi
done

cat >"$UNIT_DST" <<EOF
[Unit]
Description=ERGOMS SECURE CONNECTION (VLESS+Reality)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$WORK_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$ROOT
Environment=ERGOMS_SC_DATA=$DATA_DIR
Environment=HOME=$REAL_HOME
Environment=USER=$REAL_USER
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$EXEC_START
ExecStop=$EXEC_STOP
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStartSec=90
TimeoutStopSec=20
Nice=-5

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$UNIT_DST"

systemctl daemon-reload
systemctl enable --now "$UNIT_NAME"

echo "Installed: $UNIT_DST"
echo "Start:     systemctl enable --now ergoms-secure-connection"
echo "Status:    systemctl status ergoms-secure-connection"
echo "Logs:      journalctl -u ergoms-secure-connection -f"
if [[ -n "$FROZEN_EXE" ]]; then
  echo "Remove:    ergoms-sc uninstall-service"
else
  echo "Remove:    ./ergoms-secure-connection.sh uninstall-service"
fi
echo
systemctl --no-pager --full status ergoms-secure-connection.service || true
