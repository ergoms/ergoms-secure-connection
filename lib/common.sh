#!/usr/bin/env bash
# Shared helpers for ops-content shell scripts (Linux / macOS / Git Bash).
# shellcheck disable=SC2034

set -euo pipefail

_pk_common_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_pk_common_dir/.." && pwd)"
CONFIG_DIR="$ROOT/config"
CREDS_DIR="$ROOT/creds"
LOGS_DIR="$ROOT/logs"
VAR_DIR="$ROOT/var"
CONFIG_PATH="$ROOT/config.json"
CONFIG_EXAMPLE_PATH="$CONFIG_DIR/config.example.json"
ENV_PATH="$ROOT/.env"
ENV_LEGACY_PATH="$CREDS_DIR/.env"
CONFIG_ENC_PATH="$ROOT/config.json.enc"
KNOWN_HOSTS_PATH="$CREDS_DIR/ssh_known_hosts"
PID_PATH="$VAR_DIR/ssh.pid"
STATE_PATH="$VAR_DIR/state.json"
BRIDGE_PID_PATH="$VAR_DIR/bridge.pid"
PROXY_BACKUP_PATH="$VAR_DIR/linuxproxy.bak.json"
ENV_EXPORT_PATH="$VAR_DIR/cli.env"
CONNECT_PY="$ROOT/lib/connect_proxy.py"
PROBE_PY="$ROOT/lib/probe_connect.py"
HTTP_BRIDGE_PY="$ROOT/lib/http_via_socks.py"

_migrate_one() {
  local src="$1" dst="$2" label="$3"
  if [[ -f "$src" && ! -e "$dst" ]]; then
    mv "$src" "$dst"
    warn "moved $label"
  fi
}

harden_creds_perms() {
  # Best-effort: keep secrets readable only by the user
  chmod 700 "$CREDS_DIR" 2>/dev/null || true
  [[ -f "$CONFIG_PATH" ]] && chmod 600 "$CONFIG_PATH" 2>/dev/null || true
  [[ -f "$ENV_PATH" ]] && chmod 600 "$ENV_PATH" 2>/dev/null || true
  [[ -f "$KNOWN_HOSTS_PATH" ]] && chmod 600 "$KNOWN_HOSTS_PATH" 2>/dev/null || true
}

ensure_data_dirs() {
  mkdir -p "$CREDS_DIR/certs" "$LOGS_DIR" "$VAR_DIR" "$CONFIG_DIR"
  touch "$KNOWN_HOSTS_PATH" 2>/dev/null || true
  # Prefer root .env; pull up from legacy creds/.env if needed
  _migrate_one "$ENV_LEGACY_PATH" "$ENV_PATH" "creds/.env -> .env"
  harden_creds_perms
  for f in ssh-debug.log ssh-debug.out; do
    _migrate_one "$ROOT/$f" "$LOGS_DIR/$f" "$f -> logs/"
    _migrate_one "$ROOT/logs/$f" "$LOGS_DIR/$f" "$f -> logs/"
  done
  # legacy root runtime → var/
  _migrate_one "$ROOT/.ops-content.ssh.pid" "$PID_PATH" "ssh.pid -> var/"
  _migrate_one "$ROOT/.ops-content.bridge.pid" "$BRIDGE_PID_PATH" "bridge.pid -> var/"
  _migrate_one "$ROOT/.ops-content.state.json" "$STATE_PATH" "state -> var/"
  _migrate_one "$ROOT/.ops-content.linuxproxy.bak.json" "$PROXY_BACKUP_PATH" "linuxproxy.bak -> var/"
  _migrate_one "$ROOT/.ops-content.winproxy.bak.json" "$VAR_DIR/winproxy.bak.json" "winproxy.bak -> var/"
  _migrate_one "$ROOT/.ops-content.proxy.cmd" "$VAR_DIR/proxy.cmd" "proxy.cmd -> var/"
  _migrate_one "$ROOT/.ops-content.env" "$ENV_EXPORT_PATH" "cli.env -> var/"
  # legacy examples → config/
  _migrate_one "$ROOT/config.example.json" "$CONFIG_EXAMPLE_PATH" "config.example.json -> config/"
  # legacy helpers → lib/
  _migrate_one "$ROOT/connect_proxy.py" "$CONNECT_PY" "connect_proxy.py -> lib/"
  _migrate_one "$ROOT/probe_connect.py" "$PROBE_PY" "probe_connect.py -> lib/"
}

info() { printf '\033[36m[ops-content]\033[0m %s\n' "$*"; }
ok() { printf '\033[32m[ops-content]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ops-content]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[ops-content]\033[0m %s\n' "$*" >&2; exit 1; }

load_config_runtime() {
  # Export runtime switches from config.json (and optional legacy .env overlay)
  ensure_data_dirs
  require_config
  local py path
  py="$(find_python)"
  path="$(_py_path "$CONFIG_PATH")"
  # shellcheck disable=SC2094
  eval "$("$py" - "$path" <<'PY'
import json, sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))

def truthy(v, default=False):
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")

tun = cfg.get("tun") if isinstance(cfg.get("tun"), dict) else {}
scope = cfg.get("socks_scope") or "full"
http_port = cfg.get("http_bridge_port") or 1088
pac_port = cfg.get("pac_listen_port") or 1089
tun_on = tun.get("enabled", False)
elevate = tun.get("elevate", True)
corp = cfg.get("corporate_proxy") or "10.16.0.8:3128"
corp = str(corp).replace("http://", "").replace("https://", "").strip("/")

def sh(s):
    return "'" + str(s).replace("'", "'\\''") + "'"

print(f"export SOCKS_SCOPE={sh(str(scope).lower())}")
print(f"export HTTP_BRIDGE_PORT={sh(http_port)}")
print(f"export PAC_LISTEN_PORT={sh(pac_port)}")
print(f"export TUN={sh('1' if truthy(tun_on) else '0')}")
print(f"export TUN_ELEVATE={sh('1' if truthy(elevate, True) else '0')}")
print(f"export CORPORATE_PROXY={sh(corp)}")
PY
)"
}

# Back-compat name used by older scripts
load_dotenv() {
  if [[ -f "$CONFIG_PATH" ]]; then
    load_config_runtime
    return 0
  fi
  ensure_data_dirs
  local path="${1:-$ENV_PATH}"
  if [[ ! -f "$path" && -f "$ENV_LEGACY_PATH" ]]; then
    path="$ENV_LEGACY_PATH"
  fi
  [[ -f "$path" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *=* ]] && continue
    local key="${line%%=*}"
    local val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    [[ -z "$key" ]] && continue
    export "$key=$val"
  done <"$path"
}

get_socks_scope() {
  local s
  if [[ -z "${SOCKS_SCOPE:-}" && -f "$CONFIG_PATH" ]]; then
    s="$(config_get socks_scope)"
  else
    s="${SOCKS_SCOPE:-full}"
  fi
  s="$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')"
  case "$s" in
    full|all|system) printf 'full' ;;
    github|pac|partial) printf 'github' ;;
    *) printf 'full' ;;
  esac
}

http_bridge_port() {
  if [[ -n "${HTTP_BRIDGE_PORT:-}" ]]; then
    printf '%s' "$HTTP_BRIDGE_PORT"
    return
  fi
  if [[ -f "$CONFIG_PATH" ]]; then
    local p
    p="$(config_get http_bridge_port)"
    printf '%s' "${p:-1088}"
    return
  fi
  printf '1088'
}

get_mode() {
  # Client is VLESS+Reality only; MODE env ignored
  printf 'singbox'
}

require_config() {
  [[ -f "$CONFIG_PATH" ]] || die "Missing config.json. Run: ./ops-content.sh init"
}

_py_path() {
  # Git Bash → Windows path for native Python
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$p"
  else
    printf '%s' "$p"
  fi
}

config_get() {
  # config_get 'corporate_proxy' or 'server.host' / legacy 'ssh.host' → value
  local expr="$1" py path
  py="$(find_python)"
  path="$(_py_path "$CONFIG_PATH")"
  "$py" - "$path" "$expr" <<'PY'
import json, sys
path, expr = sys.argv[1], sys.argv[2]
data = json.loads(open(path, encoding="utf-8-sig").read())
# legacy ssh.* → server.*
parts = [p for p in expr.lstrip(".").split(".") if p]
if parts and parts[0] == "ssh":
    parts[0] = "server"
cur = data
for part in parts:
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        print("")
        raise SystemExit(0)
if isinstance(cur, bool):
    print("1" if cur else "0")
else:
    print("" if cur is None else cur)
PY
}

corporate_proxy_url() {
  local p="${CORPORATE_PROXY:-}"
  if [[ -n "$p" ]]; then
    if [[ "$p" =~ ^https?:// ]]; then
      printf '%s' "$p"
    else
      printf 'http://%s' "$p"
    fi
    return
  fi
  if [[ -f "$CONFIG_PATH" ]]; then
    p="$(config_get corporate_proxy)"
    if [[ -n "$p" ]]; then
      if [[ "$p" =~ ^https?:// ]]; then
        printf '%s' "$p"
      else
        printf 'http://%s' "$p"
      fi
      return
    fi
  fi
  printf 'http://10.16.0.8:3128'
}

apply_proxy_env() {
  local proxy
  proxy="$(corporate_proxy_url)"
  export HTTP_PROXY="$proxy" HTTPS_PROXY="$proxy"
  export http_proxy="$proxy" https_proxy="$proxy"
  export NO_PROXY='localhost,127.0.0.1'
  export npm_config_proxy="$proxy" npm_config_https_proxy="$proxy"
}

find_python() {
  local cand resolved
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    printf '%s' "$ROOT/.venv/bin/python"
    return 0
  fi
  if [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
    printf '%s' "$ROOT/.venv/Scripts/python.exe"
    return 0
  fi
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      resolved="$(command -v "$cand")"
      # skip Windows Store stub that only prints "Python" / exit 49
      case "$resolved" in
        *WindowsApps*) continue ;;
      esac
      if "$cand" -c 'import sys' >/dev/null 2>&1; then
        printf '%s' "$cand"
        return 0
      fi
    fi
  done
  die 'Python not found (need working python3)'
}

find_curl() {
  if command -v curl >/dev/null 2>&1; then
    printf '%s' curl
  else
    die 'curl not found'
  fi
}

port_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port"
    return $?
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -qE "[:.]$port[[:space:]]"
    return $?
  fi
  # fallback: try connect
  (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1
}

pid_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

write_state() {
  local json="$1"
  printf '%s\n' "$json" >"$STATE_PATH"
}

