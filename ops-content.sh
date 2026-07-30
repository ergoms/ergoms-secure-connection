#!/usr/bin/env bash
# Thin wrapper → unified Python client (same commands as Windows / EXE)
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

# Keep a few Linux-only helpers in shell (systemd / deploy / api-push)
case "$CMD" in
  install-service|uninstall-service)
    # shellcheck source=modes/socks/install-service.sh
    if [[ "$CMD" == install-service ]]; then
      bash "$ROOT/modes/socks/install-service.sh"
    else
      bash "$ROOT/modes/socks/uninstall-service.sh"
    fi
    ;;
  deploy)
    shift || true
    exec bash "$ROOT/deploy.sh" "$@"
    ;;
  api-push)
    shift || true
    exec "$PY" "$ROOT/lib/git_api_push.py" "$@"
    ;;
  *)
    export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
    exec "$PY" -m desktop "$@"
    ;;
esac
