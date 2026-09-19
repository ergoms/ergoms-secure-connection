#!/usr/bin/env bash
# Install the Linux client to /opt and put `ergoms-sc` on PATH.
# From a release folder:  sudo bash install.sh
# One-liner:              curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/linux/install.sh | sudo bash
set -euo pipefail

APP_NAME="ERGOMS SECURE CONNECTION"
APP_ID="ergoms-secure-connection"
BIN_NAME="$APP_NAME"
INSTALL_DIR="${ERGOMS_LINUX_PREFIX:-/opt/ergoms-secure-connection}"
LINK_DIR="/usr/local/bin"
DESKTOP_DST="/usr/share/applications/${APP_ID}.desktop"
REPO="${ERGOMS_LINUX_REPO:-ergoms/ergoms-secure-connection}"

usage() {
  cat <<'EOF'
usage: install.sh

Ставит клиент в /opt/ergoms-secure-connection и команду ergoms-sc.

  curl -fsSL https://raw.githubusercontent.com/ergoms/ergoms-secure-connection/main/modes/linux/install.sh | sudo bash
EOF
}

for arg in "$@"; do
  case "$arg" in
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
  echo "Нужен root: curl … | sudo bash   или   sudo bash install.sh" >&2
  exit 1
fi

CLEANUP_DIRS=()
cleanup() {
  local d
  for d in "${CLEANUP_DIRS[@]+"${CLEANUP_DIRS[@]}"}"; do
    rm -rf "$d"
  done
}
trap cleanup EXIT

need_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || {
      echo "Нужна команда: $c" >&2
      exit 1
    }
  done
}

find_local_app() {
  local src="${BASH_SOURCE[0]:-}"
  local dir dist
  if [[ -n "$src" && -f "$src" && -r "$src" ]]; then
    dir="$(cd "$(dirname "$src")" && pwd)"
    if [[ -x "$dir/$BIN_NAME" ]]; then
      echo "$dir"
      return 0
    fi
    dist="$dir/../../dist/$APP_NAME"
    if [[ -x "$dist/$BIN_NAME" ]]; then
      cd "$dist" && pwd
      return 0
    fi
  fi
  return 1
}

resolve_src() {
  local tmp api url found local_app
  if local_app="$(find_local_app)"; then
    SRC="$local_app"
    return 0
  fi
  need_cmd curl tar
  tmp="$(mktemp -d)"
  CLEANUP_DIRS+=("$tmp")
  api="https://api.github.com/repos/${REPO}/releases/latest"
  echo "==> Ищу последний linux-x64.tar.gz"
  url="$(curl -fsSL "$api" | sed -n 's/.*"browser_download_url":[[:space:]]*"\([^"]*linux-x64\.tar\.gz\)".*/\1/p' | head -n1)"
  if [[ -z "$url" ]]; then
    echo "Не удалось найти linux-x64.tar.gz в https://github.com/${REPO}/releases" >&2
    exit 1
  fi
  echo "==> Скачиваю $url"
  curl -fL --progress-bar -o "$tmp/app.tar.gz" "$url"
  tar -xzf "$tmp/app.tar.gz" -C "$tmp"
  found="$(find "$tmp" -type f -name "$BIN_NAME" | head -n1)"
  if [[ -z "$found" || ! -f "$found" ]]; then
    echo "В архиве нет бинарника $BIN_NAME" >&2
    exit 1
  fi
  chmod +x "$found"
  SRC="$(cd "$(dirname "$found")" && pwd)"
}

SRC=""
resolve_src
if [[ ! -x "$SRC/$BIN_NAME" ]]; then
  echo "нет исполняемого файла: $SRC/$BIN_NAME" >&2
  exit 1
fi

echo "==> Копирую в $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -a "$SRC"/. "$INSTALL_DIR"/
chmod +x "$INSTALL_DIR/$BIN_NAME"
if [[ -f "$INSTALL_DIR/install.sh" ]]; then
  chmod +x "$INSTALL_DIR/install.sh"
fi
if [[ -f "$INSTALL_DIR/uninstall.sh" ]]; then
  chmod +x "$INSTALL_DIR/uninstall.sh"
fi

mkdir -p "$LINK_DIR"
ln -sfn "$INSTALL_DIR/$BIN_NAME" "$LINK_DIR/ergoms-sc"
ln -sfn "$INSTALL_DIR/$BIN_NAME" "$LINK_DIR/ergoms-secure-connection"
rm -f "$LINK_DIR/ergoms"

mkdir -p "$(dirname "$DESKTOP_DST")"
cat >"$DESKTOP_DST" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=ERGOMS SECURE CONNECTION
Exec="$INSTALL_DIR/$BIN_NAME"
Path=$INSTALL_DIR
Terminal=false
Categories=Network;
StartupNotify=true
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$(dirname "$DESKTOP_DST")" 2>/dev/null || true
fi

cat <<EOF

Готово. Программа: $INSTALL_DIR

  ergoms-sc                 окно
  ergoms-sc on              подключить
  ergoms-sc off             отключить
  ergoms-sc install-service автозапуск (systemd)

Конфиг (без sudo): ~/.local/share/${APP_ID}/config.json
Снять:  sudo bash $INSTALL_DIR/uninstall.sh
        или: curl …/modes/linux/uninstall.sh | sudo bash
EOF
