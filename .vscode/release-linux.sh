#!/usr/bin/env bash
# Build Linux one-dir in a native WSL tree (not /mnt/c) and copy tar.gz to artifacts/.
set -euo pipefail

WIN_ROOT="${1:-}"
if [[ -z "$WIN_ROOT" ]]; then
  echo "usage: release-linux.sh /mnt/c/projects/ergoms-secure-connection" >&2
  exit 2
fi

if [[ ! -f "$WIN_ROOT/desktop/__init__.py" ]]; then
  echo "not a project root: $WIN_ROOT" >&2
  exit 1
fi

VER="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$WIN_ROOT/desktop/__init__.py" | head -n 1)"
if [[ -z "$VER" ]]; then
  echo "could not read __version__" >&2
  exit 1
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export DEBIAN_FRONTEND=noninteractive

if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  echo "Installing build packages (sudo -n)..."
  sudo -n apt-get update
  sudo -n apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    binutils patchelf rsync \
    libgl1 libegl1 libglib2.0-0t64 libdbus-1-3 libfontconfig1 \
    libx11-6 libxext6 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0
else
  echo "Skipping apt (sudo needs a password). Using packages already in WSL."
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found in WSL" >&2
  exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync not found in WSL" >&2
  exit 1
fi

DST="${HOME}/ergoms-secure-connection-release"
mkdir -p "$DST"
rsync -a --delete \
  --exclude '.venv/' \
  --exclude 'dist/' \
  --exclude 'build/' \
  --exclude 'tools/' \
  --exclude 'artifacts/' \
  --exclude '.git/' \
  --exclude 'var/' \
  --exclude 'logs/' \
  --exclude 'creds/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  "$WIN_ROOT/" "$DST/"

export PATH="${HOME}/.local/bin:${PATH}"
chmod +x "$DST/.vscode/setup.sh"
(cd "$DST" && ./.vscode/setup.sh build)

BIN="$DST/dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION"
if [[ ! -x "$BIN" ]]; then
  echo "missing executable: $BIN" >&2
  exit 1
fi

OUT="$WIN_ROOT/artifacts"
mkdir -p "$OUT"
TAR="$OUT/ERGOMS-SECURE-CONNECTION-${VER}-linux-x64.tar.gz"
tar -C "$DST/dist" -czf "$TAR" "ERGOMS SECURE CONNECTION"
echo "OK: $TAR"
