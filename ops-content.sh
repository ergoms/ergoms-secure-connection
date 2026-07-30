#!/usr/bin/env bash
# ops-content client (Linux / macOS / WSL / Git Bash)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"

load_dotenv "$ENV_PATH"

CMD="${1:-help}"
ARG2="${2:-}"
ARG3="${3:-443}"

clear_git_proxy() {
  git config --global --unset http.proxy 2>/dev/null || true
  git config --global --unset https.proxy 2>/dev/null || true
}

clear_instead_of() {
  local lines key
  lines="$(git config --global --get-regexp 'url\..*\.insteadof' 2>/dev/null || true)"
  [[ -z "$lines" ]] && return 0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ops-content|proxy-kill|/https/github ]]; then
      key="${line%% *}"
      git config --global --unset-all "$key" 2>/dev/null || true
    fi
  done <<<"$lines"
}

stop_http_bridge() {
  if [[ -f "$BRIDGE_PID_PATH" ]]; then
    local old
    old="$(cat "$BRIDGE_PID_PATH")"
    if pid_alive "$old"; then
      kill "$old" 2>/dev/null || true
      ok "http-bridge pid=$old stopped"
    fi
    rm -f "$BRIDGE_PID_PATH"
  fi
}

start_http_bridge() {
  require_config
  local socks bridge py corp scope mode bypass_via
  socks="$(config_get ssh.local_socks_port)"
  socks="${socks:-1080}"
  bridge="$(http_bridge_port)"
  py="$(find_python)"
  corp="$(config_get corporate_proxy)"
  scope="$(get_socks_scope)"
  mode=github
  [[ "$scope" == full ]] && mode=full
  bypass_via="$(config_get proxy_bypass_via)"
  [[ -z "$bypass_via" ]] && bypass_via=direct

  stop_http_bridge

  local -a args=(
    --listen "127.0.0.1:$bridge"
    --socks "127.0.0.1:$socks"
    --mode "$mode"
    --bypass-via "$bypass_via"
  )
  [[ -n "$corp" ]] && args+=(--fallback-proxy "$corp")

  local h
  while IFS= read -r h; do
    [[ -z "$h" ]] && continue
    args+=(--bypass-host "$h")
  done < <("$py" - "$CONFIG_PATH" <<'PY'
import json, sys
cfg = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
for h in cfg.get("proxy_bypass") or []:
    print(h)
PY
)

  if [[ "$mode" == github ]]; then
    while IFS= read -r h; do
      [[ -z "$h" ]] && continue
      args+=(--pac-host "$h")
    done < <("$py" - "$CONFIG_PATH" <<'PY'
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

  info "HTTP bridge 127.0.0.1:$bridge mode=$mode"
  "$py" "$HTTP_BRIDGE_PY" "${args[@]}" >/dev/null 2>&1 &
  local bpid=$!
  echo "$bpid" >"$BRIDGE_PID_PATH"
  local i
  for ((i = 0; i < 20; i++)); do
    sleep 0.15
    pid_alive "$bpid" || die 'http bridge exited'
    if port_listening "$bridge"; then
      printf '%s' "$bridge"
      return 0
    fi
  done
  kill "$bpid" 2>/dev/null || true
  rm -f "$BRIDGE_PID_PATH"
  die "HTTP bridge port $bridge never opened"
}

write_socks_env() {
  local scope socks bridge noproxy
  scope="$(get_socks_scope)"
  socks="$(config_get ssh.local_socks_port)"
  socks="${socks:-1080}"
  bridge="$(http_bridge_port)"
  noproxy="$(
    python3 - "$CONFIG_PATH" <<'PY'
import json, sys
cfg = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
parts = ["localhost", "127.0.0.1", "::1"]
for h in cfg.get("proxy_bypass") or []:
    h = str(h).strip()
    if h:
        parts.append(h)
print(",".join(parts))
PY
  )"
  if [[ "$scope" == full ]]; then
    cat >"$ENV_EXPORT_PATH" <<EOF
export ALL_PROXY=socks5h://127.0.0.1:${socks}
export all_proxy=socks5h://127.0.0.1:${socks}
export HTTP_PROXY=http://127.0.0.1:${bridge}
export HTTPS_PROXY=http://127.0.0.1:${bridge}
export http_proxy=http://127.0.0.1:${bridge}
export https_proxy=http://127.0.0.1:${bridge}
export NO_PROXY=${noproxy}
export no_proxy=${noproxy}
EOF
  else
    cat >"$ENV_EXPORT_PATH" <<EOF
export HTTP_PROXY=http://127.0.0.1:${bridge}
export HTTPS_PROXY=http://127.0.0.1:${bridge}
export http_proxy=http://127.0.0.1:${bridge}
export https_proxy=http://127.0.0.1:${bridge}
export NO_PROXY=${noproxy}
export no_proxy=${noproxy}
EOF
  fi
}

enable_linux_system_proxy() {
  local scope bridge
  scope="$(get_socks_scope)"
  bridge="$(http_bridge_port)"
  write_socks_env

  if ! command -v gsettings >/dev/null 2>&1; then
    ok "CLI env written: source $ENV_EXPORT_PATH"
    return 0
  fi

  # PAC respects proxy_bypass for both scopes
  gsettings set org.gnome.system.proxy mode 'auto' 2>/dev/null || true
  gsettings set org.gnome.system.proxy.autoconfig-url "http://127.0.0.1:${bridge}/proxy.pac" 2>/dev/null || true
  ok "GNOME PAC http://127.0.0.1:${bridge}/proxy.pac (scope=$scope, see config.json proxy_bypass)"
  info "CLI: source $ENV_EXPORT_PATH"
}

disable_linux_system_proxy() {
  rm -f "$ENV_EXPORT_PATH"
  if command -v gsettings >/dev/null 2>&1; then
    gsettings set org.gnome.system.proxy mode 'none' 2>/dev/null || true
    ok 'GNOME proxy disabled'
  fi
}

set_git_socks() {
  require_config
  local bridge proxy scope
  scope="$(get_socks_scope)"
  bridge="$(start_http_bridge)"
  # HTTP proxy so git + credential helpers behave; traffic still via SOCKS/VPS
  proxy="http://127.0.0.1:${bridge}"
  clear_instead_of
  git config --global http.proxy "$proxy"
  git config --global https.proxy "$proxy"
  ok "git http(s).proxy = $proxy (scope=$scope)"
  enable_linux_system_proxy
}

ssh_args() {
  require_config
  ensure_data_dirs
  local py host user port ident socks
  py="$(find_python)"
  host="$(config_get ssh.host)"
  user="$(config_get ssh.user)"
  port="$(config_get ssh.port)"
  ident="$(config_get ssh.identity_file)"
  socks="$(config_get ssh.local_socks_port)"
  port="${port:-443}"
  local proxy_cmd gkh
  proxy_cmd="$py \"$CONNECT_PY\" %h %p"
  gkh="/dev/null"
  # Pin host keys in project file (TOFU on first connect, verify afterwards)
  export OPS_CONTENT_CONNECT_ALLOW="${host}:${port}"
  local -a args=(
    -N
    -D "127.0.0.1:${socks:-1080}"
    -p "$port"
    -o BatchMode=yes
    -o ConnectTimeout=20
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
    -o ExitOnForwardFailure=yes
    -o StrictHostKeyChecking=accept-new
    -o "UserKnownHostsFile=$KNOWN_HOSTS_PATH"
    -o "GlobalKnownHostsFile=$gkh"
    -o UpdateHostKeys=yes
    -o Compression=no
    -o IPQoS=throughput
    -o TCPKeepAlive=yes
    -o Ciphers=chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com,aes128-ctr
    -o HostKeyAlgorithms=ssh-ed25519,rsa-sha2-512,rsa-sha2-256
    -o "ProxyCommand=$proxy_cmd"
  )
  if [[ -n "$ident" ]]; then
    args+=(-o IdentitiesOnly=yes -i "$ident")
    chmod 600 "$ident" 2>/dev/null || true
  fi
  args+=("${user}@${host}")
  printf '%s\0' "${args[@]}"
}

cmd_init() {
  ensure_data_dirs
  if [[ ! -f "$CONFIG_PATH" ]]; then
    cp "$CONFIG_EXAMPLE_PATH" "$CONFIG_PATH"
    ok 'Created config.json'
  else
    info 'config.json already exists'
  fi
  if [[ ! -f "$ENV_PATH" && -f "$ENV_EXAMPLE_PATH" ]]; then
    cp "$ENV_EXAMPLE_PATH" "$ENV_PATH"
    ok 'Created .env from config/.env.example'
  fi
  echo
  echo '1) Edit config.json + .env (MODE / SOCKS_SCOPE)'
  echo '2) Deploy backend:  ./deploy.sh   or  ./ops-content.sh deploy'
  echo '3) Enable:          ./ops-content.sh on'
  echo
  echo 'Layout: .env | config.json | config/ examples | creds/ keys | logs/ | var/'
  echo 'Modes: socks | vps'
}

start_tunnel() {
  require_config
  local host port socks py
  host="$(config_get ssh.host)"
  port="$(config_get ssh.port)"
  socks="$(config_get ssh.local_socks_port)"
  py="$(find_python)"
  [[ "$host" =~ YOUR_VPS ]] && die 'Set real ssh.host in config.json (and preferably ssh.port=443)'

  info "Probing CONNECT ${host}:${port} via Squid..."
  if ! "$py" "$PROBE_PY" "$host" "$port"; then
    die "CONNECT to ${host}:${port} failed (403=ACL, 503=nothing listening / firewall).
Office Squid allows *:443 but not :22. On the VPS (provider console), run:
  bash modes/vps/bootstrap_sshd_443.sh
Then: ./ops-content.sh probe ${host} 443"
  fi

  if [[ -f "$PID_PATH" ]]; then
    local old
    old="$(cat "$PID_PATH")"
    if pid_alive "$old"; then
      warn "Tunnel already running (pid=$old)"
      set_git_socks
      return 0
    fi
  fi

  export OPS_CONTENT_HTTP_PROXY
  OPS_CONTENT_HTTP_PROXY="$(config_get corporate_proxy)"
  info "SSH SOCKS -> 127.0.0.1:${socks} via Squid"

  local -a args=()
  while IFS= read -r -d '' a; do
    args+=("$a")
  done < <(ssh_args)

  ssh "${args[@]}" >/dev/null 2>&1 &
  local proc=$!
  echo "$proc" >"$PID_PATH"

  local ok_listen=0 i
  for ((i = 0; i < 40; i++)); do
    sleep 0.25
    if ! pid_alive "$proc"; then
      rm -f "$PID_PATH"
      die 'ssh exited immediately; check user/key/sshd'
    fi
    if port_listening "$socks"; then
      ok_listen=1
      break
    fi
  done
  [[ "$ok_listen" -eq 1 ]] || warn "Port $socks not listening yet"

  set_git_socks
  local bridge
  bridge="$(http_bridge_port)"
  write_state "{\"mode\":\"ssh\",\"scope\":\"$(get_socks_scope)\",\"pid\":$proc,\"socks\":\"socks5h://127.0.0.1:$socks\",\"http\":\"http://127.0.0.1:$bridge\"}"
  ok "SSH tunnel ready scope=$(get_socks_scope) (traffic via your VPS)"
}

stop_tunnel() {
  disable_linux_system_proxy
  stop_http_bridge
  if [[ -f "$PID_PATH" ]]; then
    local old
    old="$(cat "$PID_PATH")"
    if pid_alive "$old"; then
      kill "$old" 2>/dev/null || true
      ok "ssh pid=$old stopped"
    fi
    rm -f "$PID_PATH"
  fi
  clear_git_proxy
  clear_instead_of
  rm -f "$STATE_PATH"
  ok 'git proxy cleared'
}

install_service() {
  require_config
  # Avoid fighting a session-local tunnel
  if [[ -f "$PID_PATH" ]]; then
    warn 'Stopping session tunnel before enabling systemd service'
    stop_tunnel
  fi
  bash "$ROOT/modes/socks/install-service.sh"
  local bridge socks i
  bridge="$(http_bridge_port)"
  socks="$(config_get ssh.local_socks_port)"
  socks="${socks:-1080}"
  for ((i = 0; i < 40; i++)); do
    port_listening "$socks" && port_listening "$bridge" && break
    sleep 0.25
  done
  port_listening "$socks" || warn "SOCKS :$socks not up yet — check: systemctl --user status ops-content-socks"
  enable_linux_system_proxy
  git config --global http.proxy "http://127.0.0.1:${bridge}"
  git config --global https.proxy "http://127.0.0.1:${bridge}"
  ok "git + desktop proxy -> local bridge (systemd owns tunnel)"
}

uninstall_service() {
  disable_linux_system_proxy
  clear_git_proxy
  bash "$ROOT/modes/socks/uninstall-service.sh"
}

show_status() {
  local mode
  mode="$(get_mode)"
  echo "MODE (.env)       = $mode"
  echo "SOCKS_SCOPE       = $(get_socks_scope)"
  echo "git http.proxy  = $(git config --global --get http.proxy 2>/dev/null || true)"
  echo "git https.proxy = $(git config --global --get https.proxy 2>/dev/null || true)"
  local instead
  instead="$(git config --global --get-regexp 'url\..*\.insteadof' 2>/dev/null || true)"
  if [[ -n "$instead" ]]; then
    echo 'git insteadOf:'
    printf '  %s\n' "$instead"
  fi
  if [[ -f "$PID_PATH" ]]; then
    local old
    old="$(cat "$PID_PATH")"
    if pid_alive "$old"; then
      ok "ssh tunnel pid=$old running"
    else
      warn "pid=$old dead"
    fi
  fi
  [[ -f "$STATE_PATH" ]] && echo "state: $(cat "$STATE_PATH")"
  if [[ -f "$CONFIG_PATH" ]]; then
    echo "corporate_proxy = $(config_get corporate_proxy)"
    echo "ssh             = $(config_get ssh.user)@$(config_get ssh.host):$(config_get ssh.port)"
    echo "worker_base_url = $(config_get worker_base_url)"
  fi
}

test_bypass() {
  require_config
  local cproxy curlbin
  cproxy="$(config_get corporate_proxy)"
  curlbin="$(find_curl)"
  info '1) GitHub via Squid (expect 403)'
  "$curlbin" -sS -o /dev/null -w '   HTTP %{http_code}\n' -x "http://$cproxy" -m 10 -I https://github.com 2>/dev/null || true
  info '2) git ls-remote'
  GIT_TERMINAL_PROMPT=0 git ls-remote https://github.com/git/git HEAD 2>&1 | head -n 5
}

invoke_probe() {
  local host="$ARG2" port="$ARG3" py
  [[ -n "$host" ]] || die "Usage: ./ops-content.sh probe HOST [PORT]"
  py="$(find_python)"
  "$py" "$PROBE_PY" "$host" "$port" || die "probe failed"
}

enable_relay() {
  require_config
  local base
  base="$(config_get worker_base_url)"
  [[ -n "$base" ]] || die "Set worker_base_url in config.json to your VPS HTTPS relay."
  base="${base%/}"
  if [[ "$base" =~ ghfast\.top|ghproxy|kkgithub|netlify\.app|deno\.dev|pages\.dev|workers\.dev ]]; then
    die 'worker_base_url must be YOUR VPS, not a public SaaS mirror.'
  fi

  clear_git_proxy
  clear_instead_of

  local corp
  corp="$(config_get corporate_proxy)"
  git config --global "url.${base}/https/github.com/.insteadOf" 'https://github.com/'
  git config --global "url.${base}/https/api.github.com/.insteadOf" 'https://api.github.com/'
  git config --global "url.${base}/https/codeload.github.com/.insteadOf" 'https://codeload.github.com/'
  git config --global "url.${base}/https/raw.githubusercontent.com/.insteadOf" 'https://raw.githubusercontent.com/'
  git config --global "url.${base}/https/objects.githubusercontent.com/.insteadOf" 'https://objects.githubusercontent.com/'
  git config --global http.proxy "http://$corp"
  git config --global https.proxy "http://$corp"
  git config --global --unset-all http.extraHeader 2>/dev/null || true
  git config --global http.extraHeader 'Accept-Encoding: identity'
  # Host-scoped token so only the relay sees X-Ops-Content-Token
  if [[ -n "${OPS_CONTENT_SECRET:-}" ]]; then
    git config --global "http.${base}/.extraHeader" "X-Ops-Content-Token: ${OPS_CONTENT_SECRET}"
  else
    warn 'OPS_CONTENT_SECRET empty — relay auth disabled on client; set the same secret on the VPS'
  fi
  # Squid + HTTP/2 / git protocol v2 часто дают:
  #   RPC failed; curl 56 GnuTLS recv error (-110) … unexpected disconnect
  git config --global http.version HTTP/1.1
  git config --global protocol.version 1
  git config --global http.postBuffer 524288000
  # Не 999999: при залипании Squid/relay push «висит вечно»
  git config --global http.lowSpeedLimit 1000
  git config --global http.lowSpeedTime 120

  write_state "{\"mode\":\"relay\",\"base\":\"$base\"}"
  ok "Relay ON -> $base (your host)"
  info 'Check: git ls-remote https://github.com/git/git HEAD'
}

disable_relay() {
  local base
  base="$(config_get worker_base_url 2>/dev/null || true)"
  base="${base%/}"
  clear_instead_of
  clear_git_proxy
  git config --global --unset-all http.extraHeader 2>/dev/null || true
  if [[ -n "$base" ]]; then
    git config --global --unset-all "http.${base}/.extraHeader" 2>/dev/null || true
  fi
  git config --global --unset http.version 2>/dev/null || true
  git config --global --unset protocol.version 2>/dev/null || true
  git config --global --unset http.postBuffer 2>/dev/null || true
  git config --global --unset http.lowSpeedLimit 2>/dev/null || true
  git config --global --unset http.lowSpeedTime 2>/dev/null || true
  rm -f "$STATE_PATH"
  ok 'Relay OFF'
}

enable_by_mode() {
  local mode
  mode="$(get_mode)"
  info "MODE=$mode"
  case "$mode" in
    socks) start_tunnel ;;
    vps)
      require_config
      local base
      base="$(config_get worker_base_url)"
      if [[ -n "$base" && ! "$base" =~ YOUR_|ghfast|netlify|deno\.dev|pages\.dev|workers\.dev ]]; then
        enable_relay
      else
        start_tunnel
      fi
      ;;
    *) die "Unknown MODE=$mode (use socks|vps). See config/.env.example" ;;
  esac
}

disable_by_mode() {
  local mode
  mode="$(get_mode)"
  info "MODE=$mode -> off"
  case "$mode" in
    socks) stop_tunnel ;;
    vps)
      stop_tunnel
      disable_relay
      ;;
    *)
      stop_tunnel
      disable_relay
      ;;
  esac
}

invoke_deploy() {
  [[ -f "$ROOT/deploy.sh" ]] || die 'Missing deploy.sh'
  "$ROOT/deploy.sh" deploy
}

api_push() {
  require_config
  local py cwd refspec branch
  py="$(find_python)"
  cwd="${1:-.}"
  refspec="${2:-}"
  if [[ -n "$refspec" ]]; then
    "$py" "$ROOT/lib/git_api_push.py" --cwd "$cwd" --refspec "$refspec"
  else
    branch="$(git -C "$cwd" rev-parse --abbrev-ref HEAD)"
    "$py" "$ROOT/lib/git_api_push.py" --cwd "$cwd" --refspec "${branch}:${branch}"
  fi
}

show_help() {
  cat <<'EOF'
ops-content - SOCKS/VPS tunnel through corporate Squid (Linux)

Config: config.json + .env (examples in config/)

MODE=socks:
  SOCKS_SCOPE=full|github   proxy_bypass in config.json
  on / off                  tunnel + browser PAC
  install-service           systemd --user
  uninstall-service

MODE=vps: HTTPS git relay on your server (worker_base_url) or SOCKS

Other: start/stop probe status test deploy relay-on/off help
EOF
}

case "$CMD" in
  init) cmd_init ;;
  start) start_tunnel ;;
  stop) stop_tunnel ;;
  status) show_status ;;
  test) test_bypass ;;
  probe) invoke_probe ;;
  on) enable_by_mode ;;
  off) disable_by_mode ;;
  deploy) invoke_deploy ;;
  relay-on) enable_relay ;;
  relay-off) disable_relay ;;
  install-service) install_service ;;
  uninstall-service) uninstall_service ;;
  api-push) api_push "${@:2}" ;;
  help|-h|--help) show_help ;;
  *) show_help; exit 1 ;;
esac
