#!/usr/bin/env bash
# VPS mode — no automated deploy from office (Squid blocks :22)
set -euo pipefail

MODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CMD="${1:-deploy}"

echo '[vps] Open SSH on :443 on the server first (office Squid denies :22).'
echo
echo '1) On VPS (provider console / VNC), as root:'
echo "   bash $MODE_DIR/bootstrap_sshd_443.sh"
echo
echo '2) Optional HTTPS git relay (prefer with a shared secret):'
echo "   export OPS_CONTENT_SECRET=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "   python3 $MODE_DIR/github_proxy.py --port 8080"
echo "   # TLS: $MODE_DIR/Caddyfile.example"
echo '   # config.json -> worker_base_url = https://git-proxy.YOUR_DOMAIN'
echo '   # .env        -> same OPS_CONTENT_SECRET + MODE=vps'
echo
echo '3) On office PC (.env MODE=socks, ssh.port=443):'
echo '   ./ops-content.sh probe YOUR_VPS_IP 443'
echo '   ./ops-content.sh on'
echo
if [[ "$CMD" != deploy ]]; then
  echo "(command '$CMD' is a no-op for vps mode)"
fi
