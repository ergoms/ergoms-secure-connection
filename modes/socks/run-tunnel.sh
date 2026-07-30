#!/usr/bin/env bash
# Foreground SOCKS tunnel for systemd / nohup.
# Starts ssh -D, optional HTTP bridge, waits (keeps unit alive).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../lib/common.sh
source "$ROOT/lib/common.sh"

ensure_data_dirs
load_dotenv "$ENV_PATH"
require_config

SCOPE="$(printf '%s' "${SOCKS_SCOPE:-full}" | tr '[:upper:]' '[:lower:]')"
HOST="$(config_get ssh.host)"
USER_NAME="$(config_get ssh.user)"
PORT="$(config_get ssh.port)"
IDENT="$(config_get ssh.identity_file)"
SOCKS="$(config_get ssh.local_socks_port)"
SOCKS="${SOCKS:-1080}"
PORT="${PORT:-443}"
CORP="$(config_get corporate_proxy)"
BRIDGE_PORT="${HTTP_BRIDGE_PORT:-1088}"
SSH_PID=""
BRIDGE_PID=""

[[ "$HOST" =~ YOUR_VPS ]] && die 'Set real ssh.host in config.json'

export OPS_CONTENT_HTTP_PROXY="$CORP"
PY="$(find_python)"

cleanup() {
  if [[ -n "${BRIDGE_PID:-}" ]] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
  if [[ -n "${SSH_PID:-}" ]] && kill -0 "$SSH_PID" 2>/dev/null; then
    kill "$SSH_PID" 2>/dev/null || true
    wait "$SSH_PID" 2>/dev/null || true
  fi
  rm -f "$BRIDGE_PID_PATH" "$PID_PATH"
}
trap cleanup EXIT INT TERM

ensure_data_dirs
export OPS_CONTENT_CONNECT_ALLOW="${HOST}:${PORT}"

SSH_BASE=(
  -N
  -D "127.0.0.1:${SOCKS}"
  -p "$PORT"
  -o BatchMode=yes
  -o ConnectTimeout=20
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -o ExitOnForwardFailure=yes
  -o StrictHostKeyChecking=accept-new
  -o "UserKnownHostsFile=$KNOWN_HOSTS_PATH"
  -o "GlobalKnownHostsFile=/dev/null"
  -o UpdateHostKeys=yes
  -o Compression=no
  -o IPQoS=throughput
  -o TCPKeepAlive=yes
  -o "Ciphers=chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com,aes128-ctr"
  -o HostKeyAlgorithms=ssh-ed25519,rsa-sha2-512,rsa-sha2-256
  -o "ProxyCommand=$PY \"$CONNECT_PY\" %h %p"
)
if [[ -n "$IDENT" ]]; then
  SSH_BASE+=(-o IdentitiesOnly=yes -i "$IDENT")
  chmod 600 "$IDENT" 2>/dev/null || true
fi
SSH_BASE+=("${USER_NAME}@${HOST}")

info "Starting ssh SOCKS 127.0.0.1:${SOCKS} via Squid -> ${USER_NAME}@${HOST}:${PORT}"
ssh "${SSH_BASE[@]}" &
SSH_PID=$!
echo "$SSH_PID" >"$PID_PATH"

for ((i = 0; i < 80; i++)); do
  if ! kill -0 "$SSH_PID" 2>/dev/null; then
    wait "$SSH_PID" || true
    die 'ssh exited before SOCKS listen'
  fi
  if port_listening "$SOCKS"; then
    break
  fi
  sleep 0.25
done
port_listening "$SOCKS" || die "SOCKS port $SOCKS not listening"

MODE=github
[[ "$SCOPE" == "full" || "$SCOPE" == "all" ]] && MODE=full
BYPASS_VIA="$(config_get proxy_bypass_via)"
[[ -z "$BYPASS_VIA" ]] && BYPASS_VIA=direct

BRIDGE_ARGS=(
  --listen "127.0.0.1:${BRIDGE_PORT}"
  --socks "127.0.0.1:${SOCKS}"
  --mode "$MODE"
  --bypass-via "$BYPASS_VIA"
)
[[ -n "$CORP" ]] && BRIDGE_ARGS+=(--fallback-proxy "$CORP")

while IFS= read -r h; do
  [[ -z "$h" ]] && continue
  BRIDGE_ARGS+=(--bypass-host "$h")
done < <("$PY" - "$CONFIG_PATH" <<'PY'
import json, sys
cfg = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
for h in cfg.get("proxy_bypass") or []:
    print(h)
PY
)

if [[ "$MODE" == github ]]; then
  while IFS= read -r h; do
    [[ -z "$h" ]] && continue
    BRIDGE_ARGS+=(--pac-host "$h")
  done < <("$PY" - "$CONFIG_PATH" <<'PY'
import json, sys
cfg = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
seen = set()
for h in list(cfg.get("blocked_hosts") or []) + [
    "github.com", "*.github.com", "*.githubusercontent.com",
    "*.githubassets.com", "*.github.io", "ghcr.io", "*.ghcr.io",
]:
    k = h.lower()
    if k not in seen:
        seen.add(k)
        print(h)
PY
)
fi

"$PY" "$ROOT/lib/http_via_socks.py" "${BRIDGE_ARGS[@]}" &
BRIDGE_PID=$!
echo "$BRIDGE_PID" >"$BRIDGE_PID_PATH"
sleep 0.3
kill -0 "$BRIDGE_PID" 2>/dev/null || die 'http bridge failed to start'

ok "tunnel up scope=$SCOPE socks=:$SOCKS http=:$BRIDGE_PORT (ssh=$SSH_PID bridge=$BRIDGE_PID)"
wait "$SSH_PID"
exit $?
