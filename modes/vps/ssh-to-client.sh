#!/usr/bin/env bash
# Run ON the VPS after the office client has reverse-ssh up.
# Usage: bash modes/vps/ssh-to-client.sh [PORT] [USER]
set -euo pipefail

PORT="${1:-2222}"
USER="${2:-}"

if ! ss -lnt 2>/dev/null | grep -qE "127\\.0\\.0\\.1:${PORT}\\s"; then
  echo "На VPS никто не слушает 127.0.0.1:${PORT}."
  echo "На клиенте: ops-content on  и  reverse-on  (нужен ключ в creds/)."
  exit 1
fi

if [[ -n "$USER" ]]; then
  exec ssh -p "$PORT" "${USER}@127.0.0.1"
fi
exec ssh -p "$PORT" 127.0.0.1
