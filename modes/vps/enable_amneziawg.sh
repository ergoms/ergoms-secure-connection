#!/usr/bin/env bash
# Add AmneziaWG (UDP :51820) next to existing VLESS+Reality.
# Does not replace /usr/local/bin/sing-box or touch TCP :443.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

STATE_DIR=/var/lib/ops-content-singbox
CREDS="$STATE_DIR/credentials.env"
AWG_DIR=/etc/amnezia/amneziawg
CONF="$AWG_DIR/awg0.conf"
UNIT=/etc/systemd/system/ergoms-amneziawg.service
GO_VERSION="${GO_VERSION:-1.24.6}"
AWG_PORT_OVERRIDE="${AWG_PORT:-}"
SERVER_ADDR_DEFAULT="10.66.66.1/24"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

mkdir -p "$STATE_DIR" "$AWG_DIR"
touch "$CREDS"
chmod 600 "$CREDS" "$AWG_DIR"

# shellcheck disable=SC1090
source "$CREDS"

if [[ -n "$AWG_PORT_OVERRIDE" ]]; then
  AWG_PORT="$AWG_PORT_OVERRIDE"
fi
AWG_PORT="${AWG_PORT:-51820}"
if [[ "$AWG_PORT" == "443" ]]; then
  echo "WARN: AWG_PORT=443 is Reality TCP; using UDP 51820"
  AWG_PORT=51820
fi

udp_foreign_listen() {
  local line
  line="$(ss -lunp 2>/dev/null | grep -E ":${1}[[:space:]]" || true)"
  [[ -z "$line" ]] && return 1
  echo "$line" | grep -qE 'amneziawg-go|"awg"' && return 1
  return 0
}
if udp_foreign_listen "$AWG_PORT"; then
  echo "WARN: UDP :${AWG_PORT} already in use (often Docker amneziawg); picking another port"
  picked=""
  for candidate in 51821 51822 41820 24600; do
    if [[ "$candidate" == "$AWG_PORT" ]]; then
      continue
    fi
    if ! udp_foreign_listen "$candidate"; then
      picked="$candidate"
      break
    fi
  done
  if [[ -z "$picked" ]]; then
    echo "ERROR: no free UDP port for AmneziaWG" >&2
    exit 1
  fi
  AWG_PORT="$picked"
  echo "==> Using UDP :${AWG_PORT}"
fi

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) go_arch=amd64 ;;
  aarch64|arm64) go_arch=arm64 ;;
  *)
    echo "Unsupported arch: $arch" >&2
    exit 1
    ;;
esac

need_go=0
if ! command -v go >/dev/null 2>&1; then
  need_go=1
else
  go_maj="$(go env GOVERSION 2>/dev/null | sed -n 's/^go\([0-9]*\).*/\1/p')"
  if [[ -z "$go_maj" || "$go_maj" -lt 1 ]]; then
    need_go=1
  fi
fi
if [[ "$need_go" == 1 ]]; then
  echo "==> Installing Go ${GO_VERSION} (${go_arch})"
  tmp="$(mktemp -d)"
  curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz" -o "$tmp/go.tgz"
  rm -rf /usr/local/go
  tar -C /usr/local -xzf "$tmp/go.tgz"
  rm -rf "$tmp"
fi
export PATH="/usr/local/go/bin:${PATH:-/usr/bin}"

install_awg_bins() {
  if command -v amneziawg-go >/dev/null 2>&1 && command -v awg >/dev/null 2>&1 && command -v awg-quick >/dev/null 2>&1; then
    return 0
  fi
  echo "==> Building amneziawg-go + amneziawg-tools"
  apt-get update -y >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y git make gcc >/dev/null 2>&1 || true
  src="$(mktemp -d)"
  git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-go "$src/go"
  ( cd "$src/go" && make )
  bin="$(find "$src/go" -type f -name amneziawg-go | head -n1)"
  [[ -n "$bin" && -x "$bin" ]] || { echo "amneziawg-go build failed" >&2; exit 1; }
  install -m 755 "$bin" /usr/local/bin/amneziawg-go
  git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-tools "$src/tools"
  ( cd "$src/tools/src" && make && make install )
  rm -rf "$src"
  command -v awg >/dev/null 2>&1 || { echo "awg missing after tools install" >&2; exit 1; }
  command -v awg-quick >/dev/null 2>&1 || { echo "awg-quick missing after tools install" >&2; exit 1; }
}

install_awg_bins

rand_u32() {
  python3 - <<'PY'
import secrets
print(secrets.randbelow(0xFFFFFFFE) + 1)
PY
}

upsert_cred() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$CREDS" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$CREDS"
  else
    printf '%s=%s\n' "$key" "$value" >>"$CREDS"
  fi
}

if [[ -z "${AWG_SERVER_PRIVATE:-}" ]]; then
  AWG_SERVER_PRIVATE="$(awg genkey)"
fi
AWG_SERVER_PUBLIC="$(printf '%s\n' "$AWG_SERVER_PRIVATE" | awg pubkey)"
AWG_ADDRESS_SERVER="${AWG_ADDRESS_SERVER:-$SERVER_ADDR_DEFAULT}"

if [[ -z "${AWG_JC:-}" || "$AWG_JC" == "0" ]]; then
  AWG_JC="$(python3 -c 'import secrets; print(secrets.randbelow(8)+3)')"
fi
if [[ -z "${AWG_JMIN:-}" || "$AWG_JMIN" == "0" ]]; then
  AWG_JMIN="$(python3 -c 'import secrets; print(secrets.randbelow(25)+40)')"
fi
if [[ -z "${AWG_JMAX:-}" || "$AWG_JMAX" == "0" ]]; then
  AWG_JMAX="$(python3 -c "print($AWG_JMIN + 20 + (__import__('secrets').randbelow(40)))")"
fi
if [[ -z "${AWG_S1:-}" || "$AWG_S1" == "0" ]]; then
  AWG_S1="$(python3 -c 'import secrets; print(secrets.randbelow(80)+15)')"
fi
if [[ -z "${AWG_S2:-}" || "$AWG_S2" == "0" ]]; then
  AWG_S2="$(python3 -c 'import secrets; print(secrets.randbelow(80)+15)')"
fi
if [[ -z "${AWG_H1:-}" ]]; then AWG_H1="$(rand_u32)"; fi
if [[ -z "${AWG_H2:-}" ]]; then AWG_H2="$(rand_u32)"; fi
if [[ -z "${AWG_H3:-}" ]]; then AWG_H3="$(rand_u32)"; fi
if [[ -z "${AWG_H4:-}" ]]; then AWG_H4="$(rand_u32)"; fi

WAN_IFACE="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
WAN_IFACE="${WAN_IFACE:-eth0}"

upsert_cred AWG_PORT "$AWG_PORT"
upsert_cred AWG_SERVER_PRIVATE "$AWG_SERVER_PRIVATE"
upsert_cred AWG_SERVER_PUBLIC "$AWG_SERVER_PUBLIC"
upsert_cred AWG_ADDRESS_SERVER "$AWG_ADDRESS_SERVER"
upsert_cred AWG_JC "$AWG_JC"
upsert_cred AWG_JMIN "$AWG_JMIN"
upsert_cred AWG_JMAX "$AWG_JMAX"
upsert_cred AWG_S1 "$AWG_S1"
upsert_cred AWG_S2 "$AWG_S2"
upsert_cred AWG_H1 "$AWG_H1"
upsert_cred AWG_H2 "$AWG_H2"
upsert_cred AWG_H3 "$AWG_H3"
upsert_cred AWG_H4 "$AWG_H4"

echo "==> Client keys → $ROOT/creds/awg"
python3 "$ROOT/modes/vps/awg_clients.py" ensure

echo "==> Writing $CONF (iface $WAN_IFACE, UDP :$AWG_PORT)"
python3 "$ROOT/modes/vps/awg_clients.py" emit

sysctl -w net.ipv4.ip_forward=1 >/dev/null
mkdir -p /etc/sysctl.d
printf 'net.ipv4.ip_forward=1\n' >/etc/sysctl.d/99-ergoms-awg.conf

AWG_QUICK_BIN="$(command -v awg-quick)"
cat >"$UNIT" <<EOF
[Unit]
Description=ERGOMS SECURE CONNECTION AmneziaWG
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=${AWG_QUICK_BIN} up awg0
ExecStop=${AWG_QUICK_BIN} down awg0

[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null 2>&1; then
  ufw allow "${AWG_PORT}/udp" || true
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${AWG_PORT}/udp" || true
  firewall-cmd --reload || true
fi
iptables -C INPUT -p udp --dport "$AWG_PORT" -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p udp --dport "$AWG_PORT" -j ACCEPT || true

systemctl daemon-reload
systemctl enable ergoms-amneziawg.service
if systemctl is-active --quiet ergoms-amneziawg.service; then
  systemctl restart ergoms-amneziawg.service
else
  systemctl start ergoms-amneziawg.service
fi
sleep 1
ss -lunp 2>/dev/null | grep -E ":${AWG_PORT}\\b" || echo "WARN: UDP :$AWG_PORT not listening"

cat <<EOF

========================================================================
OK: AmneziaWG UDP :${AWG_PORT} (sing-box Reality не трогали)
Ключи в репозитории: $ROOT/creds/awg/
  client.json     — Reality
  pc.conf         — первый AWG (и pc2.conf, phone.conf, …)
Добавить ещё: bash modes/vps/add_amneziawg_client.sh phone
              bash modes/vps/add_amneziawg_client.sh --count 5
В клиенте: Из файла (client.json), затем Загрузить .conf.
На VPS в панели хостинга откройте UDP ${AWG_PORT}.
========================================================================
EOF
