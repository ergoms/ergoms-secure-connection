#!/usr/bin/env bash
# Thin wrapper → unified Python client
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

find_python() {
  local c
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c "import sys" 2>/dev/null; then
        echo "$c"
        return 0
      fi
    fi
  done
  echo "Python 3 not found" >&2
  exit 1
}

PY="$(find_python)"
CMD="${1:-help}"

case "$CMD" in
  install-service|uninstall-service)
    if [[ "$CMD" == install-service ]]; then
      exec bash "$ROOT/modes/linux/install-service.sh"
    else
      exec bash "$ROOT/modes/linux/uninstall-service.sh"
    fi
    ;;
  deploy)
    shift || true
    exec bash "$ROOT/deploy.sh" "$@"
    ;;
  *)
    export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
    exec "$PY" -m desktop "$@"
    ;;
esac
