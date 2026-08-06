#!/usr/bin/env bash
# Run ONCE on the VPS (provider console / VNC / out-of-band SSH).
# Squid at the office only allows CONNECT to :443; :22 is denied.
# After this, from office PC: .\ops-content.ps1 probe YOUR_IP 443
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

dropin=/etc/ssh/sshd_config.d/99-ops-content-443.conf
mkdir -p /etc/ssh/sshd_config.d

# Keep :22 for out-of-band access from the provider console.
# Harden auth only when root already has an authorized key (avoid lockout).
harden=""
if [[ -s /root/.ssh/authorized_keys ]]; then
  harden=$(cat <<'H'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
AuthenticationMethods publickey
H
)
  echo "Root authorized_keys found — disabling password login for sshd."
else
  echo "WARNING: no /root/.ssh/authorized_keys — password login left enabled."
  echo "Add a key, then re-run this script to harden auth."
fi

cat >"$dropin" <<EOF
Port 22
Port 443
AllowTcpForwarding yes
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
$harden
EOF

if command -v ufw >/dev/null 2>&1; then
  ufw allow 443/tcp || true
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=443/tcp || true
  firewall-cmd --reload || true
fi
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p tcp --dport 443 -j ACCEPT || true

# Socket-activated ssh (Ubuntu): regenerate ListenStream from Port lines.
if systemctl daemon-reload 2>/dev/null; then
  systemctl restart ssh.socket ssh.service 2>/dev/null \
    || systemctl restart sshd.socket sshd.service 2>/dev/null \
    || systemctl restart ssh.service 2>/dev/null \
    || systemctl restart sshd.service 2>/dev/null \
    || service ssh restart 2>/dev/null \
    || service sshd restart
else
  systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null \
    || service ssh restart 2>/dev/null || service sshd restart
fi
echo "sshd listening on 22 and 443"

ss -lntp | grep -E ':443|:22' || netstat -lntp 2>/dev/null | grep -E ':443|:22' || true
echo
echo "NOTE: if you also run Caddy/nginx on :443, use a second IP or an SNI/SSH multiplexer;"
echo "      sshd and a TLS web server cannot both own the same address:443."
echo "OK. From office: .\\ops-content.ps1 probe $(curl -4 -s ifconfig.me || hostname -I | awk '{print $1}') 443"
