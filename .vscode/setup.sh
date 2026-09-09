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

case "${1:-all}" in
  libraries) install_libraries ;;
  sing-box) install_singbox ;;
  all)
    install_libraries
    install_singbox
    ;;
  *)
    echo "usage: setup.sh [all|libraries|sing-box]" >&2
    exit 2
    ;;
esac
