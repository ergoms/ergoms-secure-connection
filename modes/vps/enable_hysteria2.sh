#!/usr/bin/env bash
# Add Hysteria2 (UDP :443) next to existing VLESS+Reality (TCP :443).
# Home Wi-Fi DPI eats Reality TLS; QUIC/Hysteria2 is the same class as Amnezia.
# Office keeps using VLESS through Squid CONNECT — this inbound is unused there.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

CONF_DIR=/etc/sing-box
STATE_DIR=/var/lib/ops-content-singbox
CREDS="$STATE_DIR/credentials.env"
CERT="$CONF_DIR/hy2.crt"
KEY="$CONF_DIR/hy2.key"
# UDP 443 looks like HTTP/3 and home DPI often blackholes it.
# TCP 443 stays VLESS; this is a different protocol on 8443.
HY2_PORT_OVERRIDE="${HY2_PORT:-}"

if [[ ! -f "$CONF_DIR/config.json" ]]; then
  echo "ERROR: $CONF_DIR/config.json missing — run bootstrap_singbox_443.sh first" >&2
  exit 1
fi
if ! command -v sing-box >/dev/null 2>&1 && [[ ! -x /usr/local/bin/sing-box ]]; then
  echo "ERROR: sing-box not installed" >&2
  exit 1
fi
SB="$(command -v sing-box || true)"
SB="${SB:-/usr/local/bin/sing-box}"

mkdir -p "$STATE_DIR" "$CONF_DIR"
touch "$CREDS"
chmod 600 "$CREDS"

# shellcheck disable=SC1090
source "$CREDS"
# Env wins, then credentials.env, then 8443. Never 443 — that is VLESS TCP / DPI-blackholed UDP.
if [[ -n "$HY2_PORT_OVERRIDE" ]]; then
  HY2_PORT="$HY2_PORT_OVERRIDE"
fi
HY2_PORT="${HY2_PORT:-8443}"
if [[ "$HY2_PORT" == "443" ]]; then
  echo "WARN: HY2_PORT=443 is Reality TCP; using UDP 8443"
  HY2_PORT=8443
fi
SERVER_NAME="${REALITY_SERVER_NAME:-${SERVER_NAME:-www.cloudflare.com}}"

if [[ -z "${HY2_PASSWORD:-}" ]]; then
  HY2_PASSWORD="$(openssl rand -hex 16)"
  if grep -q '^HY2_PASSWORD=' "$CREDS" 2>/dev/null; then
    sed -i "s/^HY2_PASSWORD=.*/HY2_PASSWORD=$HY2_PASSWORD/" "$CREDS"
  else
    printf 'HY2_PASSWORD=%s\n' "$HY2_PASSWORD" >>"$CREDS"
  fi
fi
if grep -q '^HY2_PORT=' "$CREDS" 2>/dev/null; then
  sed -i "s/^HY2_PORT=.*/HY2_PORT=$HY2_PORT/" "$CREDS"
else
  printf 'HY2_PORT=%s\n' "$HY2_PORT" >>"$CREDS"
fi

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  echo "==> Self-signed cert for Hysteria2 (SNI $SERVER_NAME)"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout "$KEY" -out "$CERT" -days 3650 -nodes \
    -subj "/CN=$SERVER_NAME" >/dev/null 2>&1
  chmod 600 "$KEY" "$CERT"
fi

echo "==> Merging hysteria2 inbound (UDP :$HY2_PORT)"
python3 - "$CONF_DIR/config.json" "$HY2_PASSWORD" "$HY2_PORT" "$SERVER_NAME" "$CERT" "$KEY" <<'PY'
import json, sys
path, password, port, sni, cert, key = sys.argv[1:]
cfg = json.load(open(path, encoding="utf-8"))
ins = cfg.setdefault("inbounds", [])
hy = next((x for x in ins if str(x.get("type") or "") == "hysteria2"), None)
if hy is not None:
    hy["listen_port"] = int(port)
    users = hy.get("users") or [{"name": "ergoms", "password": password}]
    if users and isinstance(users[0], dict):
        users[0]["password"] = password
    hy["users"] = users
    json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
    open(path, "a", encoding="utf-8").write("\n")
    print(f"hysteria2 inbound moved to UDP :{port}")
else:
    ins.append({
        "type": "hysteria2",
        "tag": "hy2-in",
        "listen": "::",
        "listen_port": int(port),
        "users": [{"name": "ergoms", "password": password}],
        "tls": {
            "enabled": True,
            "server_name": sni,
            "alpn": ["h3"],
            "certificate_path": cert,
            "key_path": key,
        },
        "masquerade": f"https://{sni}",
    })
    route = cfg.setdefault("route", {})
    rules = route.setdefault("rules", [])
    if not any(r.get("inbound") == ["hy2-in"] for r in rules if isinstance(r, dict)):
        rules.insert(0, {"inbound": ["hy2-in"], "action": "sniff", "timeout": "1s"})
    json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
    open(path, "a", encoding="utf-8").write("\n")
    print("added hy2-in")
PY
chmod 600 "$CONF_DIR/config.json"

echo "==> Validating"
"$SB" check -c "$CONF_DIR/config.json"

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${HY2_PORT}/udp" || true
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${HY2_PORT}/udp" || true
  firewall-cmd --reload || true
fi
iptables -C INPUT -p udp --dport "$HY2_PORT" -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p udp --dport "$HY2_PORT" -j ACCEPT || true

systemctl restart sing-box.service
sleep 1
ss -lunp 2>/dev/null | grep -E ":${HY2_PORT}\\b" || echo "WARN: UDP :$HY2_PORT not listening"

cat <<EOF

========================================================================
OK: Hysteria2 UDP :${HY2_PORT} (VLESS+Reality TCP :443 не трогали)

В config.json клиента, внутрь transport:

  "hysteria2": {
    "password": "$HY2_PASSWORD",
    "port": $HY2_PORT,
    "server_name": "$SERVER_NAME",
    "insecure": true
  }

Офис (corporate=true) по-прежнему идёт VLESS через Squid.
Дома клиент сам возьмёт Hysteria2.
========================================================================
EOF
