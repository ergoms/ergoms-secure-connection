#!/usr/bin/env bash
# One-shot VPS setup: VLESS+Reality on :443 and AmneziaWG on UDP :51820.
# From a repo checkout:  sudo bash modes/vps/install.sh
# From hosting console:  curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/vps/install.sh | bash
set -euo pipefail

REPO_URL="${ERGOMS_VPS_REPO:-https://github.com/ergoms/ergoms-secure-connection.git}"
DEST="${ERGOMS_VPS_ROOT:-/opt/ergoms-secure-connection}"
CLIENT_JSON="/var/lib/ops-content-singbox/client.json"
ENABLE_AWG=1

usage() {
  cat <<'EOF'
usage: install.sh [--no-awg]

  --no-awg   только VLESS+Reality (TCP 443), без AmneziaWG
EOF
}

for arg in "$@"; do
  case "$arg" in
    --no-awg) ENABLE_AWG=0 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  src="${BASH_SOURCE[0]:-}"
  if [[ -n "$src" && -f "$src" && -r "$src" ]]; then
    exec sudo "$src" "$@"
  fi
  echo "Нужен root: curl … | sudo bash   или   sudo bash modes/vps/install.sh" >&2
  exit 1
fi

ensure_pkg() {
  local missing=()
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  [[ ${#missing[@]} -eq 0 ]] && return 0
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y "${missing[@]}"
    return 0
  fi
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y "${missing[@]}"
    return 0
  fi
  if command -v yum >/dev/null 2>&1; then
    yum install -y "${missing[@]}"
    return 0
  fi
  echo "Нужны пакеты: ${missing[*]}" >&2
  exit 1
}

resolve_root() {
  local src="${BASH_SOURCE[0]:-}"
  local here
  if [[ -n "$src" && -f "$src" && -r "$src" ]]; then
    here="$(cd "$(dirname "$src")" && pwd)"
    if [[ -f "$here/bootstrap_singbox_443.sh" ]]; then
      cd "$here/../.." && pwd
      return 0
    fi
  fi
  if [[ -f "$DEST/modes/vps/bootstrap_singbox_443.sh" ]]; then
    echo "$DEST"
    return 0
  fi
  ensure_pkg git
  if [[ -d "$DEST/.git" ]]; then
    echo "==> Обновляю $DEST" >&2
    git -C "$DEST" pull --ff-only || true
  elif [[ -e "$DEST" ]]; then
    echo "ERROR: $DEST уже есть и это не репозиторий" >&2
    exit 1
  else
    echo "==> Клонирую $REPO_URL → $DEST" >&2
    git clone --depth 1 "$REPO_URL" "$DEST"
  fi
  echo "$DEST"
}

ensure_pkg curl python3
ROOT="$(resolve_root)"
BOOTSTRAP="$ROOT/modes/vps/bootstrap_singbox_443.sh"
AWG="$ROOT/modes/vps/enable_amneziawg.sh"
[[ -x "$BOOTSTRAP" || -f "$BOOTSTRAP" ]] || { echo "нет $BOOTSTRAP" >&2; exit 1; }

echo "==> VLESS+Reality на TCP :443"
bash "$BOOTSTRAP"

if [[ "$ENABLE_AWG" == 1 ]]; then
  echo "==> AmneziaWG на UDP :51820"
  bash "$AWG"
fi

AWG_PORT="51820"
if [[ -f /var/lib/ops-content-singbox/credentials.env ]]; then
  # shellcheck disable=SC1091
  AWG_PORT="$(awk -F= '/^AWG_PORT=/{print $2; exit}' /var/lib/ops-content-singbox/credentials.env || true)"
  AWG_PORT="${AWG_PORT:-51820}"
fi

cat <<EOF

========================================================================
Готово.

  Офис:  VLESS+Reality  TCP 443
  Дом:   AmneziaWG      UDP ${AWG_PORT}

Конфиг для ПК:
  $CLIENT_JSON

В панели хостинга откройте TCP 443$([ "$ENABLE_AWG" == 1 ] && printf ' и UDP %s' "$AWG_PORT").
На компьютере: Настройки → Из файла.

Ещё устройства (AWG):
  bash $ROOT/modes/vps/add_amneziawg_client.sh phone
========================================================================
EOF
