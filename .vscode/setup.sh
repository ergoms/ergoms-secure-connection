#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

find_system_python() {
  local c
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' 2>/dev/null; then
      echo "$c"
      return 0
    fi
  done
  echo "Python 3 not found (нужен 3.10–3.14)" >&2
  exit 1
}

find_poetry() {
  local c
  export PATH="${HOME}/.local/bin:${PATH}"
  if command -v poetry >/dev/null 2>&1; then
    command -v poetry
    return 0
  fi
  for c in "${HOME}/.local/bin/poetry"; do
    if [[ -x "$c" ]]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

install_poetry() {
  echo "Poetry не найден — ставлю…"
  local py installer
  py="$(find_system_python)"
  installer="${TMPDIR:-/tmp}/install-poetry.py"
  "$py" -c "import urllib.request; urllib.request.urlretrieve('https://install.python-poetry.org', r'''$installer''')"
  "$py" "$installer"
  find_poetry
}

install_libraries() {
  local poetry
  poetry="$(find_poetry || true)"
  if [[ -z "${poetry:-}" ]]; then
    poetry="$(install_poetry)"
  fi
  echo "Poetry: $poetry"
  "$poetry" install --extras gui
}

install_singbox() {
  local py
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    py="${ROOT}/.venv/bin/python"
  elif [[ -x "${ROOT}/.venv/Scripts/python.exe" ]]; then
    py="${ROOT}/.venv/Scripts/python.exe"
  else
    echo ".venv не найден — сначала поставьте библиотеки (Poetry)" >&2
    exit 1
  fi
  "$py" -m desktop download-sing-box
}

run_pyinstaller() {
  local poetry sb
  poetry="$(find_poetry || true)"
  if [[ -z "${poetry:-}" ]]; then
    poetry="$(install_poetry)"
  fi
  "$poetry" install --extras gui --extras build
  if [[ -x "${ROOT}/tools/sing-box" ]]; then
    sb="${ROOT}/tools/sing-box"
  elif [[ -x "${ROOT}/tools/sing-box.exe" ]]; then
    sb="${ROOT}/tools/sing-box.exe"
  else
    install_singbox
  fi
  echo "PyInstaller: ErgomsVPN.spec -> dist/ErgomsVPN"
  "$poetry" run pyinstaller --noconfirm --clean ErgomsVPN.spec
  if [[ -f "${ROOT}/dist/ErgomsVPN.exe" ]]; then
    echo "OK: ${ROOT}/dist/ErgomsVPN.exe"
  elif [[ -f "${ROOT}/dist/ErgomsVPN" ]]; then
    echo "OK: ${ROOT}/dist/ErgomsVPN"
  else
    echo "pyinstaller finished but dist/ErgomsVPN is missing" >&2
    exit 1
  fi
}

case "${1:-setup}" in
  libraries) install_libraries ;;
  sing-box) install_singbox ;;
  pyinstaller) run_pyinstaller ;;
  setup|all)
    install_libraries
    install_singbox
    ;;
  build|exe)
    install_libraries
    install_singbox
    run_pyinstaller
    ;;
  *)
    echo "usage: setup.sh [setup|build]" >&2
    exit 2
    ;;
esac
