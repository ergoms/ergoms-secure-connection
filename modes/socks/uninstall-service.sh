#!/usr/bin/env bash
# Backward-compatible wrapper → current VLESS systemd uninstaller.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/modes/linux/uninstall-service.sh" "$@"
