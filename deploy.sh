#!/usr/bin/env bash
# Deploy hints — VLESS+Reality only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[deploy] VLESS+Reality (sing-box) on VPS :443"
echo 'On VPS (console):'
echo '  bash modes/vps/disable_sshd_443.sh'
echo '  bash modes/vps/bootstrap_singbox_443.sh'
echo 'On PC: paste transport into config.json (server.host = VPS IP)'
echo '  ./ergoms-vpn.sh probe HOST 443'
echo '  ./ergoms-vpn.sh on'
exit 0
