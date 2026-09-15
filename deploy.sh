#!/usr/bin/env bash
# VPS setup — delegates to modes/vps/install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/modes/vps/install.sh" "$@"
