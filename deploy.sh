#!/usr/bin/env bash
# Deploy dispatcher by MODE — socks / singbox (server bootstrap hints)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"

load_dotenv "$ENV_PATH"

MODE="$(get_mode)"

case "$MODE" in
  socks)
    warn "MODE=socks — open SSH on VPS :443, then CLI client on."
    echo 'On VPS (console): bash modes/vps/bootstrap_sshd_443.sh'
    echo 'On Linux: ./ops-content.sh probe HOST 443 ; ./ops-content.sh on'
    echo 'On Windows: .\\ops-content.ps1 probe HOST 443 ; .\\ops-content.ps1 on'
    echo '  (or: python -m desktop on)'
    exit 0
    ;;
  singbox)
    warn "MODE=singbox — sing-box VLESS+Reality on VPS :443; CLI client on."
    echo 'On VPS (console):'
    echo '  bash modes/vps/disable_sshd_443.sh'
    echo '  bash modes/vps/bootstrap_singbox_443.sh'
    echo 'On PC: paste transport into config.json; set MODE=singbox TUN=1 in .env'
    echo '  ./ops-content.sh on   or   .\\ops-content.ps1 on   or   python -m desktop on'
    echo '  probe: ./ops-content.sh probe HOST 443'
    exit 0
    ;;
  *)
    die "Unknown MODE=$MODE (use socks|singbox). See config/.env.example"
    ;;
esac
