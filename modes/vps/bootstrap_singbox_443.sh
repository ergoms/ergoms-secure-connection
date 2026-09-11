#!/usr/bin/env bash
# Install sing-box VLESS+Reality on :443 (office clients reach it via Squid CONNECT).
# Run on the VPS from provider console / non-office SSH — not through office Squid
# while sshd still owns :443.
#
# After success, copy the printed transport block into an external VLESS client
# (or keep it in config.json for reference).
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SING_BOX_VERSION="${SING_BOX_VERSION:-1.11.15}"
SERVER_NAME="${REALITY_SERVER_NAME:-www.cloudflare.com}"
INSTALL_DIR=/usr/local/bin
CONF_DIR=/etc/sing-box
STATE_DIR=/var/lib/ops-content-singbox
UNIT=/etc/systemd/system/sing-box.service

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch_tag=amd64 ;;
  aarch64|arm64) arch_tag=arm64 ;;
  *)
    echo "Unsupported arch: $arch" >&2
    exit 1
    ;;
esac

echo "==> Freeing :443 from sshd (if ERGOMS SECURE CONNECTION drop-in present)"
if [[ -x "$ROOT/modes/vps/disable_sshd_443.sh" ]]; then
  bash "$ROOT/modes/vps/disable_sshd_443.sh" || true
else
  for dropin in \
    /etc/ssh/sshd_config.d/99-ops-content-443.conf \
    /etc/ssh/sshd_config.d/99.ops-content-443.conf; do
    if [[ -f "$dropin" ]]; then
      rm -f "$dropin"
    fi
  done
  systemctl daemon-reload 2>/dev/null || true
  systemctl restart ssh.socket ssh.service 2>/dev/null \
    || systemctl restart sshd.socket sshd.service 2>/dev/null \
    || systemctl restart ssh.service 2>/dev/null \
    || systemctl restart sshd.service 2>/dev/null || true
fi

if ss -lntp 2>/dev/null | grep -qE ':443\b' || netstat -lntp 2>/dev/null | grep -qE ':443\b'; then
  echo "ERROR: something is already listening on :443. Stop it, then re-run." >&2
  ss -lntp 2>/dev/null | grep -E ':443' || true
  exit 1
fi

echo "==> Installing sing-box v${SING_BOX_VERSION} (${arch_tag})"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
asset="sing-box-${SING_BOX_VERSION}-linux-${arch_tag}.tar.gz"
url="https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/${asset}"
curl -fsSL -o "$tmp/$asset" "$url"
tar -xzf "$tmp/$asset" -C "$tmp"
bin="$(find "$tmp" -type f -name sing-box | head -n1)"
[[ -n "$bin" && -x "$bin" ]] || { echo "sing-box binary missing in archive" >&2; exit 1; }
install -m 755 "$bin" "$INSTALL_DIR/sing-box"
"$INSTALL_DIR/sing-box" version

mkdir -p "$CONF_DIR" "$STATE_DIR"
CREDS="$STATE_DIR/credentials.env"

if [[ -f "$CREDS" ]]; then
  # shellcheck disable=SC1090
  source "$CREDS"
  echo "==> Reusing credentials from $CREDS"
else
  echo "==> Generating UUID + Reality keypair"
  UUID="$("$INSTALL_DIR/sing-box" generate uuid)"
  # Output: PrivateKey: xxx \n PublicKey: yyy
  kp="$("$INSTALL_DIR/sing-box" generate reality-keypair)"
  PRIVATE_KEY="$(printf '%s\n' "$kp" | awk -F': ' '/PrivateKey/{print $2; exit}')"
  PUBLIC_KEY="$(printf '%s\n' "$kp" | awk -F': ' '/PublicKey/{print $2; exit}')"
  SHORT_ID="$("$INSTALL_DIR/sing-box" generate rand 8 --hex 2>/dev/null || true)"
  if [[ -z "$SHORT_ID" ]]; then
    SHORT_ID="$(openssl rand -hex 4 2>/dev/null || head -c 16 /dev/urandom | xxd -p | head -c 8)"
  fi
  [[ -n "$UUID" && -n "$PRIVATE_KEY" && -n "$PUBLIC_KEY" && -n "$SHORT_ID" ]] \
    || { echo "Failed to generate credentials" >&2; exit 1; }
  cat >"$CREDS" <<EOF
UUID=$UUID
PRIVATE_KEY=$PRIVATE_KEY
PUBLIC_KEY=$PUBLIC_KEY
SHORT_ID=$SHORT_ID
SERVER_NAME=$SERVER_NAME
EOF
  chmod 600 "$CREDS"
fi

# Re-read in case SERVER_NAME overridden on re-run
# shellcheck disable=SC1090
source "$CREDS"
SERVER_NAME="${REALITY_SERVER_NAME:-$SERVER_NAME}"

echo "==> Writing $CONF_DIR/config.json"
cat >"$CONF_DIR/config.json" <<EOF
{
  "log": {
    "level": "info",
    "timestamp": true
  },
  "inbounds": [
    {
      "type": "vless",
      "tag": "vless-in",
      "listen": "::",
      "listen_port": 443,
      "users": [
        {
          "uuid": "$UUID",
          "flow": "xtls-rprx-vision"
        }
      ],
      "tls": {
        "enabled": true,
        "server_name": "$SERVER_NAME",
        "reality": {
          "enabled": true,
          "handshake": {
            "server": "$SERVER_NAME",
            "server_port": 443
          },
          "private_key": "$PRIVATE_KEY",
          "short_id": ["", "$SHORT_ID"]
        }
      }
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ],
  "route": {
    "rules": [
      {
        "inbound": ["vless-in"],
        "action": "sniff",
        "timeout": "1s"
      }
    ],
    "final": "direct"
  }
}
EOF
chmod 600 "$CONF_DIR/config.json"

echo "==> Validating config"
"$INSTALL_DIR/sing-box" check -c "$CONF_DIR/config.json"

echo "==> systemd unit"
cat >"$UNIT" <<EOF
[Unit]
Description=sing-box (ERGOMS SECURE CONNECTION VLESS Reality)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/sing-box run -c $CONF_DIR/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now sing-box.service

if [[ -f "$ROOT/modes/vps/enable_hysteria2.sh" ]]; then
  echo "==> Hysteria2 (UDP :443) for home Wi-Fi"
  bash "$ROOT/modes/vps/enable_hysteria2.sh" || true
fi

if command -v ufw >/dev/null 2>&1; then
  ufw allow 443/tcp || true
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=443/tcp || true
  firewall-cmd --reload || true
fi
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p tcp --dport 443 -j ACCEPT || true

sleep 1
systemctl --no-pager --full status sing-box.service || true
ss -lntp 2>/dev/null | grep -E ':443' || true

# shellcheck disable=SC1090
source "$CREDS"
PUBLIC_IP="$(curl -4 -fsS --max-time 8 ifconfig.me 2>/dev/null || true)"
if [[ -z "$PUBLIC_IP" ]]; then
  PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
PUBLIC_IP="${PUBLIC_IP:-YOUR_VPS_IP}"

cat <<EOF

========================================================================
OK: sing-box VLESS+Reality listening on :443

On the office PC — config.json (merge into existing file):

  "server": {
    "host": "$PUBLIC_IP",
    "port": 443,
    "local_socks_port": 1080
  },
  "transport": {
    "type": "vless-reality",
    "uuid": "$UUID",
    "public_key": "$PUBLIC_KEY",
    "short_id": "$SHORT_ID",
    "server_name": "$SERVER_NAME",
    "port": 443,
    "hysteria2": {
      "password": "${HY2_PASSWORD:-}",
      "port": ${HY2_PORT:-443},
      "server_name": "$SERVER_NAME",
      "insecure": true
    }
  }

On the office PC — merge server + transport into config.json, then:

  # config.json
  "tun": { "enabled": true, "elevate": true, ... }

  ./ergoms-secure-connection.sh on
  # or: .\\ergoms-secure-connection.ps1 on
  # or: python -m desktop on

  # optional: encrypt config for transfer to another PC
  # ./ergoms-secure-connection.sh encrypt
  # ./ergoms-secure-connection.sh decrypt config.json.enc

Probe from office:

  ./ergoms-secure-connection.sh probe $PUBLIC_IP 443
  # or: .\\ergoms-secure-connection.ps1 probe $PUBLIC_IP 443

Credentials saved on VPS: $CREDS
========================================================================
EOF
