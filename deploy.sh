#!/usr/bin/env bash
# Deploy dispatcher by MODE (Linux) — only socks / vps
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"

load_dotenv "$ENV_PATH"

CMD="${1:-deploy}"
TOKEN="${2:-}"
if [[ "$CMD" == set-token && -n "${2:-}" ]]; then
  TOKEN="$2"
fi

MODE="$(get_mode)"

case "$MODE" in
  socks)
    warn "MODE=socks — open SSH on VPS :443, then client on."
    echo 'On VPS (console): bash modes/vps/bootstrap_sshd_443.sh'
    echo 'On PC: ./ops-content.sh probe HOST 443 ; ./ops-content.sh on'
    exit 0
    ;;
  vps)
    script="$ROOT/modes/vps/deploy.sh"
    info "MODE=vps -> $script"
    "$script" "$CMD" "$TOKEN"
    ;;
  *)
    die "Unknown MODE=$MODE (use socks|vps). See config/.env.example"
    ;;
esac
