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
  echo "Poetry не найден — ставлю…" >&2
  local py installer
  py="$(find_system_python)"
  installer="${TMPDIR:-/tmp}/install-poetry.py"
  "$py" -c "import urllib.request; urllib.request.urlretrieve('https://install.python-poetry.org', r'''$installer''')"
  "$py" "$installer" >&2
  find_poetry
}

repair_project_venv() {
  local cfg command expected
  cfg="${ROOT}/.venv/pyvenv.cfg"
  [[ -f "$cfg" ]] || return 0
  command="$(sed -n 's/^[[:space:]]*command[[:space:]]*=[[:space:]]*//p' "$cfg" | head -n 1)"
  [[ -n "$command" ]] || return 0
  expected="${ROOT}/.venv"
  case "$command" in
    *"$expected"*) return 0 ;;
  esac
  echo "[ERGOMS SECURE CONNECTION] .venv belongs to another project — recreating"
  rm -rf "${ROOT}/.venv"
}

venv_python() {
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    echo "${ROOT}/.venv/bin/python"
  elif [[ -x "${ROOT}/.venv/Scripts/python.exe" ]]; then
    echo "${ROOT}/.venv/Scripts/python.exe"
  else
    echo ".venv не найден — сначала поставьте библиотеки (Poetry)" >&2
    exit 1
  fi
}

install_libraries() {
  local poetry
  repair_project_venv
  poetry="$(find_poetry || true)"
  if [[ -z "${poetry:-}" ]]; then
    poetry="$(install_poetry)"
  fi
  echo "Poetry: $poetry"
  "$poetry" install --extras gui
}

install_singbox() {
  local py
  py="$(venv_python)"
  "$py" -m desktop download-sing-box
  "$py" -m desktop download-sing-box-awg
}

run_pyinstaller() {
  local poetry py
  repair_project_venv
  poetry="$(find_poetry || true)"
  if [[ -z "${poetry:-}" ]]; then
    poetry="$(install_poetry)"
  fi
  "$poetry" install --extras gui --extras build
  if [[ ! -x "${ROOT}/tools/sing-box" && ! -x "${ROOT}/tools/sing-box.exe" ]] \
     || [[ ! -x "${ROOT}/tools/sing-box-awg" && ! -f "${ROOT}/tools/ergoms-tun-awg.exe" ]]; then
    install_singbox
  fi
  py="$(venv_python)"
  echo "PyInstaller: ErgomsSecureConnection.spec -> dist/ERGOMS SECURE CONNECTION/"
  "$py" -m PyInstaller --noconfirm --clean ErgomsSecureConnection.spec
  if [[ -f "${ROOT}/dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION.exe" ]]; then
    echo "OK: ${ROOT}/dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION.exe"
  elif [[ -x "${ROOT}/dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION" ]]; then
    echo "OK: ${ROOT}/dist/ERGOMS SECURE CONNECTION/ERGOMS SECURE CONNECTION"
  else
    echo "pyinstaller finished but dist/ERGOMS SECURE CONNECTION/ is missing" >&2
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
    echo 'Setup OK'
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
