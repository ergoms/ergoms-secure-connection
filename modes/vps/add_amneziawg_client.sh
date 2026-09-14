#!/usr/bin/env bash
# Add AmneziaWG client keys into creds/awg/ and reload the server.
#   bash modes/vps/add_amneziawg_client.sh phone laptop
#   bash modes/vps/add_amneziawg_client.sh --count 5
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 "$ROOT/modes/vps/awg_clients.py" add "$@"
