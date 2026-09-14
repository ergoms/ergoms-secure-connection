"""Load/save config.json (single source of truth); migrate legacy .env; encrypt helpers."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from desktop.logutil import noop
from desktop.paths import Paths, bundle_dir


LogFn = Callable[[str], None]

CORPORATE_PROXY_PRESET = "192.0.2.10:3128"
CORPORATE_BYPASS_PRESET = ["*.intranet.example", "*.local", "*.lan"]
STANDARD_BYPASS_PRESET = ["*.local", "*.lan"]

# Reality dest must look like a real site the VPS can handshake with.
REALITY_DEFAULT_SNI = "www.cloudflare.com"
# QUIC Initial SNI is plaintext to TSPU. Cloudflare is on the RU block/throttle
# list; do not reuse Reality dest here.
HY2_DEFAULT_SNI = "www.microsoft.com"
AWG_DEFAULT_PORT = 51820
AWG_DEFAULT_ADDRESS = "10.66.66.2/32"
AWG_DEFAULT_MTU = 1280
_BLOCKED_HY2_SNI = frozenset(
    {
        "www.cloudflare.com",
        "cloudflare.com",
        "cloudflare-dns.com",
        "1.1.1.1",
        "one.one.one.one",
    }
)


def normalize_dial(value: Any) -> str:
    """Reality, Hysteria2, or AmneziaWG. Legacy `auto` becomes Hysteria2 (home)."""
    raw = str(value or "").strip().lower()
    if raw in ("vless", "reality", "vless-reality"):
        return "vless-reality"
    if raw in ("hy2", "hysteria2"):
        return "hysteria2"
    if raw in ("amneziawg", "awg", "wireguard", "wg"):
        return "amneziawg"
    return "hysteria2"


def default_amneziawg_block() -> dict[str, Any]:
    return {
        "port": AWG_DEFAULT_PORT,
        "private_key": "",
        "peer_public_key": "",
        "pre_shared_key": "",
        "address": AWG_DEFAULT_ADDRESS,
        "mtu": AWG_DEFAULT_MTU,
        "jc": 0,
        "jmin": 0,
        "jmax": 0,
        "s1": 0,
        "s2": 0,
        "h1": "",
        "h2": "",
        "h3": "",
        "h4": "",
        "keepalive": 25,
    }


def parse_amnezia_conf(text: str) -> dict[str, Any]:
    """Parse wg-quick / Amnezia .conf into host + amneziawg fields."""
    section = ""
    iface: dict[str, str] = {}
    peer: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key_l = key.strip().lower()
        val_s = val.strip().strip('"').strip("'")
        if section == "interface":
            iface[key_l] = val_s
        elif section == "peer":
            peer[key_l] = val_s

    def _int_field(src: dict[str, str], name: str, default: int = 0) -> int:
        raw = str(src.get(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw.split()[0], 10)
        except ValueError:
            return default

    endpoint = str(peer.get("endpoint") or "").strip()
    host = ""
    port = AWG_DEFAULT_PORT
    if endpoint:
        if endpoint.startswith("["):
            br = endpoint.find("]")
            host = endpoint[1:br].strip() if br > 0 else ""
            rest = endpoint[br + 1 :].lstrip(":") if br > 0 else ""
            port = _as_int(rest, AWG_DEFAULT_PORT)
        else:
            host, sep, port_s = endpoint.rpartition(":")
            if sep:
                host = host.strip()
                port = _as_int(port_s, AWG_DEFAULT_PORT)
            else:
                host = endpoint
    address = str(iface.get("address") or AWG_DEFAULT_ADDRESS).split(",")[0].strip()
    mtu = _int_field(iface, "mtu", AWG_DEFAULT_MTU)
    keepalive = _int_field(peer, "persistentkeepalive", 25) or 25
    return {
        "host": host,
        "port": max(1, min(65535, port)),
        "private_key": str(iface.get("privatekey") or "").strip(),
        "peer_public_key": str(peer.get("publickey") or "").strip(),
        "pre_shared_key": str(peer.get("presharedkey") or "").strip(),
        "address": address or AWG_DEFAULT_ADDRESS,
        "mtu": max(1280, min(1500, mtu or AWG_DEFAULT_MTU)),
        "jc": _int_field(iface, "jc"),
        "jmin": _int_field(iface, "jmin"),
        "jmax": _int_field(iface, "jmax"),
        "s1": _int_field(iface, "s1"),
        "s2": _int_field(iface, "s2"),
        "h1": str(iface.get("h1") or "").strip(),
        "h2": str(iface.get("h2") or "").strip(),
        "h3": str(iface.get("h3") or "").strip(),
        "h4": str(iface.get("h4") or "").strip(),
        "keepalive": max(0, min(600, keepalive)),
    }


def looks_like_wg_conf(text: str) -> bool:
    low = str(text or "").lower()
    return "[interface]" in low and "privatekey" in low


def apply_amnezia_to_config(cfg: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """Merge wg-quick / Amnezia fields into client config.json (other dials stay)."""
    out = ensure_config_defaults(cfg)
    awg = out["transport"].setdefault("amneziawg", {})
    if not isinstance(awg, dict):
        awg = {}
        out["transport"]["amneziawg"] = awg
    for key in (
        "private_key",
        "peer_public_key",
        "pre_shared_key",
        "address",
        "port",
        "mtu",
        "jc",
        "jmin",
        "jmax",
        "s1",
        "s2",
        "h1",
        "h2",
        "h3",
        "h4",
        "keepalive",
    ):
        val = parsed.get(key)
        if val in (None, ""):
            continue
        awg[key] = val
    host = str(parsed.get("host") or "").strip()
    if host:
        out.setdefault("server", {})
        if isinstance(out["server"], dict):
            out["server"]["host"] = host
    return ensure_config_defaults(out)


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def merge_imported_config(
    base: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Fold a full or partial client JSON into one file. Empty values do not wipe keys."""
    src = deepcopy(incoming if isinstance(incoming, dict) else {})
    keys = set(src)
    fragment_keys = {
        "amneziawg",
        "hysteria2",
        "uuid",
        "public_key",
        "short_id",
        "server_name",
        "dial",
        "type",
    }
    if keys & fragment_keys and "transport" not in src and "server" not in src:
        wrap: dict[str, Any] = {}
        for key in ("amneziawg", "hysteria2"):
            if key in src:
                wrap[key] = src.pop(key)
        for key in ("uuid", "public_key", "short_id", "server_name", "port", "dial", "type"):
            if key in src:
                wrap[key] = src.pop(key)
        src = {"transport": wrap, **src}

    if base is None:
        return ensure_config_defaults(src)

    out = deepcopy(base)
    inc_tr = src.get("transport")
    if isinstance(inc_tr, dict):
        dst_tr = out.setdefault("transport", {})
        if not isinstance(dst_tr, dict):
            dst_tr = {}
            out["transport"] = dst_tr
        for key, val in inc_tr.items():
            if key in ("hysteria2", "amneziawg") and isinstance(val, dict):
                cur = dst_tr.get(key)
                if not isinstance(cur, dict):
                    cur = {}
                for sub, subval in val.items():
                    if _nonempty(subval):
                        cur[sub] = subval
                dst_tr[key] = cur
            elif _nonempty(val):
                dst_tr[key] = val
    for key, val in src.items():
        if key == "transport":
            continue
        if (
            key in ("server", "tun", "reverse_ssh")
            and isinstance(val, dict)
            and isinstance(out.get(key), dict)
        ):
            merged = dict(out[key])
            for sub, subval in val.items():
                if _nonempty(subval):
                    merged[sub] = subval
            out[key] = merged
        elif _nonempty(val):
            out[key] = val
    return ensure_config_defaults(out)


def config_is_ready(cfg: dict[str, Any] | None) -> bool:
    """True if host + at least one transport (Reality / Hy2 / AWG) is filled."""
    if not isinstance(cfg, dict):
        return False
    host = str((cfg.get("server") or {}).get("host") or "").strip()
    if not host or "YOUR_VPS" in host.upper():
        return False
    tr = cfg.get("transport")
    if not isinstance(tr, dict):
        return False
    uuid = str(tr.get("uuid") or "").strip()
    if uuid and "REPLACE" not in uuid.upper() and len(uuid) >= 8:
        return True
    hy = tr.get("hysteria2") if isinstance(tr.get("hysteria2"), dict) else {}
    if str(hy.get("password") or "").strip():
        return True
    awg = tr.get("amneziawg") if isinstance(tr.get("amneziawg"), dict) else {}
    priv = str(awg.get("private_key") or "").strip()
    pub = str(awg.get("peer_public_key") or "").strip()
    return bool(priv and pub and "REPLACE" not in priv.upper())


def normalize_hy2_sni(value: Any, *, fallback: str = HY2_DEFAULT_SNI) -> str:
    sni = str(value or "").strip()
    if not sni or sni.lower() in _BLOCKED_HY2_SNI:
        return fallback
    return sni

# Legacy .env keys → config.json (migration only)
_ENV_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})


def _truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val or "").strip().lower() in _ENV_BOOL_TRUE


def _as_bool(val: Any, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    return _truthy(val)


def _as_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def load_dotenv(path: Path) -> dict[str, str]:
    """Read a legacy .env file (migration / optional overrides)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        i = line.find("=")
        if i < 1:
            continue
        key = line[:i].strip()
        val = line[i + 1 :].strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


_config_cache: tuple[float, dict[str, Any]] | None = None
_runtime_cfg: dict[str, Any] | None = None


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        return 0.0


def invalidate_config_cache() -> None:
    """Drop cached config.json reads (after save/update)."""
    global _config_cache, _runtime_cfg
    _config_cache = None
    _runtime_cfg = None


def default_config_template() -> dict[str, Any]:
    return {
        "corporate": False,
        "use_proxy": False,
        "corporate_proxy": "",
        "socks_scope": "full",
        "http_bridge_port": 1088,
        "pac_listen_port": 1089,
        "watchdog": True,
        "watchdog_interval": 15,
        "watchdog_max_retries": 5,
        "kill_switch": True,
        "git_proxy": False,
        "docker_proxy": False,
        "server": {
            "host": "YOUR_VPS_IP_OR_HOSTNAME",
            "port": 443,
            "local_socks_port": 1080,
        },
        "blocked_hosts": [
            "github.com",
            "www.github.com",
            "api.github.com",
            "codeload.github.com",
            "ssh.github.com",
            "gist.github.com",
            "ghcr.io",
            "objects.githubusercontent.com",
            "raw.githubusercontent.com",
            "github.githubassets.com",
            "avatars.githubusercontent.com",
            "packages.github.com",
            "cursor.com",
            "*.cursor.com",
            "*.cursor.sh",
            "*.cursor-cdn.com",
            "*.cursorapi.com",
            "*.cursorvm.com",
            "downloads.cursor.com",
            "marketplace.cursorapi.com",
            "api2.cursor.sh",
            "api3.cursor.sh",
            "api4.cursor.sh",
            "api5.cursor.sh",
            "authenticate.cursor.sh",
            "authenticator.cursor.sh",
        ],
        "proxy_bypass": list(STANDARD_BYPASS_PRESET),
        "proxy_bypass_via": "direct",
        "tun": {
            "enabled": True,
            "elevate": True,
            "sing_box_path": "",
            "mtu": 1500,
        },
        "transport": {
            "type": "vless-reality",
            "dial": "hysteria2",
            "uuid": "",
            "public_key": "",
            "short_id": "",
            "server_name": REALITY_DEFAULT_SNI,
            "port": 443,
            "hysteria2": {
                "password": "",
                "port": 8443,
                "server_name": HY2_DEFAULT_SNI,
                "obfs_password": "",
                "insecure": True,
            },
            "amneziawg": default_amneziawg_block(),
        },
        "reverse_ssh": {
            "enabled": True,
            "vps_user": "root",
            "vps_port": 22,
            "listen_port": 2222,
            "local_port": 22,
            "identity_file": "",
        },
    }


def migrate_env_into_config(cfg: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Fold legacy .env keys into config (env wins only when key was present)."""
    out = deepcopy(cfg)
    tun = out.setdefault("tun", {})
    if not isinstance(tun, dict):
        tun = {}
        out["tun"] = tun

    if "SOCKS_SCOPE" in env and env["SOCKS_SCOPE"].strip():
        out["socks_scope"] = env["SOCKS_SCOPE"].strip().lower()
    if "HTTP_BRIDGE_PORT" in env and env["HTTP_BRIDGE_PORT"].strip():
        out["http_bridge_port"] = _as_int(env["HTTP_BRIDGE_PORT"], 1088)
    if "PAC_LISTEN_PORT" in env and env["PAC_LISTEN_PORT"].strip():
        out["pac_listen_port"] = _as_int(env["PAC_LISTEN_PORT"], 1089)
    if "CORPORATE_PROXY" in env and env["CORPORATE_PROXY"].strip():
        p = env["CORPORATE_PROXY"].strip()
        out["corporate_proxy"] = p.replace("http://", "").replace("https://", "").strip("/")
    if "WATCHDOG" in env:
        out["watchdog"] = _as_bool(env["WATCHDOG"], True)
    if "WATCHDOG_INTERVAL" in env and env["WATCHDOG_INTERVAL"].strip():
        out["watchdog_interval"] = max(5, _as_int(env["WATCHDOG_INTERVAL"], 15))
    if "WATCHDOG_MAX_RETRIES" in env and env["WATCHDOG_MAX_RETRIES"].strip():
        out["watchdog_max_retries"] = max(1, _as_int(env["WATCHDOG_MAX_RETRIES"], 5))
    if "KILL_SWITCH" in env:
        out["kill_switch"] = _as_bool(env["KILL_SWITCH"], True)
    if "TUN" in env:
        tun["enabled"] = _as_bool(env["TUN"], True)
    if "TUN_ELEVATE" in env:
        tun["elevate"] = _as_bool(env["TUN_ELEVATE"], True)
    if "SING_BOX_PATH" in env and env["SING_BOX_PATH"].strip():
        tun["sing_box_path"] = env["SING_BOX_PATH"].strip()
    if "TUN_MTU" in env and env["TUN_MTU"].strip():
        mtu = _as_int(env["TUN_MTU"], 1500)
        tun["mtu"] = max(1280, min(1500, mtu))
    if "REVERSE_SSH" in env:
        rev = out.setdefault("reverse_ssh", {})
        if isinstance(rev, dict):
            rev["enabled"] = _as_bool(env["REVERSE_SSH"], True)
    return out


def infer_corporate(cfg: dict[str, Any] | None) -> bool:
    """Office profile only when explicitly enabled — leftover proxy fields are ignored."""
    if not cfg:
        return False
    return _as_bool(cfg.get("corporate"), False)


def apply_corporate_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    """Office defaults: GitHub+Cursor via proxy, university bypass."""
    out = cfg
    out["corporate"] = True
    out["use_proxy"] = True
    out["socks_scope"] = "github"
    out["corporate_proxy"] = CORPORATE_PROXY_PRESET
    out["proxy_bypass"] = list(CORPORATE_BYPASS_PRESET)
    out.setdefault("proxy_bypass_via", "direct")
    out["git_proxy"] = True
    out["docker_proxy"] = True
    tr = out.setdefault("transport", {})
    if isinstance(tr, dict):
        tr["dial"] = "vless-reality"
    return out


def apply_standard_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ordinary VPN: all traffic, no office proxy."""
    out = cfg
    out["corporate"] = False
    out["use_proxy"] = False
    out["socks_scope"] = "full"
    out["corporate_proxy"] = ""
    out["proxy_bypass"] = list(STANDARD_BYPASS_PRESET)
    out["proxy_bypass_via"] = "direct"
    return out


def ensure_config_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Fill missing sections without wiping user values."""
    tmpl = default_config_template()
    out = deepcopy(cfg) if cfg else {}
    if "corporate" not in out:
        out["corporate"] = False
    out.setdefault("use_proxy", False)
    out.setdefault("corporate_proxy", tmpl["corporate_proxy"])
    out.setdefault("socks_scope", tmpl["socks_scope"])
    out.setdefault("http_bridge_port", tmpl["http_bridge_port"])
    out.setdefault("pac_listen_port", tmpl["pac_listen_port"])
    out.setdefault("watchdog", tmpl["watchdog"])
    out.setdefault("watchdog_interval", tmpl["watchdog_interval"])
    out.setdefault("watchdog_max_retries", tmpl["watchdog_max_retries"])
    out.setdefault("kill_switch", tmpl["kill_switch"])
    if "git_proxy" not in out:
        out["git_proxy"] = _as_bool(out.get("corporate"), False)
    else:
        out["git_proxy"] = _as_bool(out.get("git_proxy"), False)
    if "docker_proxy" not in out:
        out["docker_proxy"] = _as_bool(out.get("corporate"), False)
    else:
        out["docker_proxy"] = _as_bool(out.get("docker_proxy"), False)

    # Migrate legacy ssh{} → server{} (SSH tunnel mode removed)
    legacy = out.pop("ssh", None)
    server = out.get("server")
    if not isinstance(server, dict):
        server = {}
    if isinstance(legacy, dict):
        for key in ("host", "port", "local_socks_port"):
            if key in legacy and key not in server:
                server[key] = legacy[key]
    out["server"] = server
    server.setdefault("host", "YOUR_VPS_IP_OR_HOSTNAME")
    server.setdefault("port", 443)
    server.setdefault("local_socks_port", 1080)
    server.pop("user", None)
    server.pop("identity_file", None)

    out.pop("worker_base_url", None)
    out.setdefault("blocked_hosts", tmpl["blocked_hosts"])
    out.setdefault("proxy_bypass", tmpl["proxy_bypass"])
    out.setdefault("proxy_bypass_via", tmpl["proxy_bypass_via"])

    tun = out.setdefault("tun", {})
    if not isinstance(tun, dict):
        tun = {}
        out["tun"] = tun
    # Legacy top-level TUN-like keys
    if "enabled" not in tun and "tun_enabled" in out:
        tun["enabled"] = out.pop("tun_enabled")
    tun.setdefault("enabled", True)
    tun.setdefault("elevate", True)
    tun.setdefault("sing_box_path", "")
    tun.setdefault("mtu", 1500)
    tun["enabled"] = _as_bool(tun.get("enabled"), True)
    tun["elevate"] = _as_bool(tun.get("elevate"), True)

    transport = out.setdefault("transport", {})
    if not isinstance(transport, dict):
        transport = {}
        out["transport"] = transport
    transport.setdefault("type", "vless-reality")
    transport["dial"] = normalize_dial(transport.get("dial"))
    transport.setdefault("uuid", "")
    transport.setdefault("public_key", "")
    transport.setdefault("short_id", "")
    transport.setdefault("server_name", REALITY_DEFAULT_SNI)
    transport.setdefault("port", 443)
    hy = transport.get("hysteria2")
    if not isinstance(hy, dict):
        hy = {}
        transport["hysteria2"] = hy
    hy.setdefault("password", "")
    hy.setdefault("port", 8443)
    hy["server_name"] = normalize_hy2_sni(hy.get("server_name"))
    hy.setdefault("obfs_password", "")
    hy.setdefault("insecure", True)
    hy["insecure"] = _as_bool(hy.get("insecure"), True)
    hy["port"] = max(1, min(65535, _as_int(hy.get("port"), 8443)))
    awg = transport.get("amneziawg")
    if not isinstance(awg, dict):
        awg = {}
        transport["amneziawg"] = awg
    tmpl_awg = default_amneziawg_block()
    for key, val in tmpl_awg.items():
        awg.setdefault(key, val)
    awg["port"] = max(1, min(65535, _as_int(awg.get("port"), AWG_DEFAULT_PORT)))
    awg["mtu"] = max(1280, min(1500, _as_int(awg.get("mtu"), AWG_DEFAULT_MTU)))
    awg["keepalive"] = max(0, min(600, _as_int(awg.get("keepalive"), 25)))
    for junk in ("jc", "jmin", "jmax", "s1", "s2"):
        awg[junk] = max(0, _as_int(awg.get(junk), 0))
    awg["private_key"] = str(awg.get("private_key") or "").strip()
    awg["peer_public_key"] = str(awg.get("peer_public_key") or "").strip()
    awg["pre_shared_key"] = str(awg.get("pre_shared_key") or "").strip()
    awg["address"] = str(awg.get("address") or AWG_DEFAULT_ADDRESS).strip() or AWG_DEFAULT_ADDRESS
    for hdr in ("h1", "h2", "h3", "h4"):
        awg[hdr] = str(awg.get(hdr) or "").strip()

    rev = out.setdefault("reverse_ssh", {})
    if not isinstance(rev, dict):
        rev = {}
        out["reverse_ssh"] = rev
    rev.setdefault("enabled", True)
    rev.setdefault("vps_user", "root")
    rev.setdefault("vps_port", 22)
    rev.setdefault("listen_port", 2222)
    rev.setdefault("local_port", 22)
    rev.setdefault("identity_file", "")
    rev["enabled"] = _as_bool(rev.get("enabled"), True)
    rev["vps_port"] = max(1, min(65535, _as_int(rev.get("vps_port"), 22)))
    rev["listen_port"] = max(1, min(65535, _as_int(rev.get("listen_port"), 2222)))
    rev["local_port"] = max(1, min(65535, _as_int(rev.get("local_port"), 22)))
    rev["vps_user"] = str(rev.get("vps_user") or "root").strip() or "root"
    rev["identity_file"] = str(rev.get("identity_file") or "").strip()

    # Normalize scope
    scope = str(out.get("socks_scope") or "full").strip().lower()
    if scope in ("full", "all", "system"):
        out["socks_scope"] = "full"
    elif scope in ("github", "pac", "partial"):
        out["socks_scope"] = "github"
    else:
        out["socks_scope"] = "full"

    out["corporate"] = _as_bool(out.get("corporate"), False)
    out["use_proxy"] = _as_bool(out.get("use_proxy"), False) or out["corporate"]
    out["http_bridge_port"] = _as_int(out.get("http_bridge_port"), 1088)
    out["pac_listen_port"] = _as_int(out.get("pac_listen_port"), 1089)
    out["watchdog"] = _as_bool(out.get("watchdog"), True)
    out["watchdog_interval"] = max(5, _as_int(out.get("watchdog_interval"), 15))
    out["watchdog_max_retries"] = max(1, _as_int(out.get("watchdog_max_retries"), 5))
    out["kill_switch"] = _as_bool(out.get("kill_switch"), True)
    out["git_proxy"] = _as_bool(out.get("git_proxy"), False)
    out["docker_proxy"] = _as_bool(out.get("docker_proxy"), False)
    mtu = tun.get("mtu")
    tun["mtu"] = max(1280, min(1500, _as_int(mtu, 1500)))
    return out


def load_config(path: Path, *, force: bool = False) -> dict[str, Any]:
    global _config_cache
    if not path.is_file():
        raise FileNotFoundError(f"Missing config.json. Run init first: {path}")
    mtime = _file_mtime(path)
    if not force and _config_cache and _config_cache[0] == mtime:
        return deepcopy(_config_cache[1])
    cfg = ensure_config_defaults(json.loads(path.read_text(encoding="utf-8-sig")))
    _config_cache = (mtime, cfg)
    return deepcopy(cfg)


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(ensure_config_defaults(cfg), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    invalidate_config_cache()


def apply_config(path: Path, *, force: bool = False, env_path: Path | None = None) -> dict[str, Any]:
    """Load config.json into runtime cache and mirror key values to os.environ.

    env_path is ignored (kept for call-site compat). Legacy .env is merged only in init.
    """
    global _runtime_cfg
    del env_path  # legacy .env must not override config.json at runtime
    cfg = load_config(path, force=force)
    _runtime_cfg = cfg
    _mirror_to_environ(cfg)
    return deepcopy(cfg)


def _mirror_to_environ(cfg: dict[str, Any]) -> None:
    """Keep process env in sync for shell helpers / child processes."""
    tun = cfg.get("tun") if isinstance(cfg.get("tun"), dict) else {}
    os.environ["SOCKS_SCOPE"] = str(cfg.get("socks_scope") or "full")
    os.environ["HTTP_BRIDGE_PORT"] = str(cfg.get("http_bridge_port") or 1088)
    os.environ["PAC_LISTEN_PORT"] = str(cfg.get("pac_listen_port") or 1089)
    os.environ["TUN"] = "1" if _as_bool(tun.get("enabled"), True) else "0"
    os.environ["TUN_ELEVATE"] = "1" if _as_bool(tun.get("elevate"), True) else "0"
    os.environ["WATCHDOG"] = "1" if _as_bool(cfg.get("watchdog"), True) else "0"
    os.environ["WATCHDOG_INTERVAL"] = str(cfg.get("watchdog_interval") or 15)
    os.environ["WATCHDOG_MAX_RETRIES"] = str(cfg.get("watchdog_max_retries") or 5)
    os.environ["KILL_SWITCH"] = "1" if _as_bool(cfg.get("kill_switch"), True) else "0"
    corp = resolve_corporate_proxy(cfg)
    if corp:
        os.environ["CORPORATE_PROXY"] = corp
    else:
        os.environ.pop("CORPORATE_PROXY", None)


def update_config_key(path: Path, key: str, value: Any) -> None:
    """Set one config field (supports TUN / SOCKS_SCOPE legacy names)."""
    cfg = load_config(path) if path.is_file() else default_config_template()
    key_u = str(key).strip()
    tun = cfg.setdefault("tun", {})
    if not isinstance(tun, dict):
        tun = {}
        cfg["tun"] = tun

    mapping = {
        "TUN": ("tun.enabled", value),
        "TUN_ELEVATE": ("tun.elevate", value),
        "SOCKS_SCOPE": ("socks_scope", value),
        "HTTP_BRIDGE_PORT": ("http_bridge_port", value),
        "PAC_LISTEN_PORT": ("pac_listen_port", value),
        "CORPORATE_PROXY": ("corporate_proxy", value),
        "WATCHDOG": ("watchdog", value),
        "WATCHDOG_INTERVAL": ("watchdog_interval", value),
        "WATCHDOG_MAX_RETRIES": ("watchdog_max_retries", value),
        "KILL_SWITCH": ("kill_switch", value),
        "SING_BOX_PATH": ("tun.sing_box_path", value),
        "TUN_MTU": ("tun.mtu", value),
        "REVERSE_SSH": ("reverse_ssh.enabled", value),
    }
    if key_u in mapping:
        target, raw = mapping[key_u]
        if target == "tun.enabled":
            tun["enabled"] = _as_bool(raw, True)
        elif target == "kill_switch":
            cfg["kill_switch"] = _as_bool(raw, True)
        elif target == "tun.elevate":
            tun["elevate"] = _as_bool(raw, True)
        elif target == "tun.sing_box_path":
            tun["sing_box_path"] = str(raw or "").strip()
        elif target == "tun.mtu":
            tun["mtu"] = max(1280, min(1500, _as_int(raw, 1500)))
        elif target == "socks_scope":
            cfg["socks_scope"] = str(raw or "full").strip().lower()
        elif target == "corporate_proxy":
            p = str(raw or "").strip()
            cfg["corporate_proxy"] = p.replace("http://", "").replace("https://", "").strip("/")
        elif target == "watchdog":
            cfg["watchdog"] = _as_bool(raw, True)
        elif target == "reverse_ssh.enabled":
            r = cfg.setdefault("reverse_ssh", {})
            if not isinstance(r, dict):
                r = {}
                cfg["reverse_ssh"] = r
            r["enabled"] = _as_bool(raw, False)
        elif target in ("http_bridge_port", "pac_listen_port", "watchdog_interval", "watchdog_max_retries"):
            cfg[target] = _as_int(raw, cfg.get(target) or 0)
    elif key_u.startswith("reverse_ssh."):
        r = cfg.setdefault("reverse_ssh", {})
        if not isinstance(r, dict):
            r = {}
            cfg["reverse_ssh"] = r
        sub = key_u.split(".", 1)[1]
        if sub == "enabled":
            r[sub] = _as_bool(value, False)
        elif sub in ("vps_port", "listen_port", "local_port"):
            r[sub] = max(1, min(65535, _as_int(value, 22)))
        else:
            r[sub] = value
    elif key_u.startswith("tun."):
        sub = key_u.split(".", 1)[1]
        if sub in ("enabled", "elevate"):
            tun[sub] = _as_bool(value, True)
        elif sub == "mtu":
            tun["mtu"] = max(1280, min(1500, _as_int(value, 1500)))
        else:
            tun[sub] = value
    else:
        cfg[key_u] = value

    if key_u == "TUN" and "elevate" not in tun:
        tun["elevate"] = True
    save_config(path, cfg)
    apply_config(path, force=True)


def _runtime() -> dict[str, Any]:
    if _runtime_cfg is not None:
        return _runtime_cfg
    return {}


def get_server(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    if not cfg:
        return {
            "host": "YOUR_VPS_IP_OR_HOSTNAME",
            "port": 443,
            "local_socks_port": 1080,
        }
    server = cfg.get("server")
    if isinstance(server, dict):
        return server
    return {}


def get_server_host(cfg: dict[str, Any] | None = None) -> str:
    return str(get_server(cfg).get("host") or "").strip()


def get_local_socks_port(cfg: dict[str, Any] | None = None) -> int:
    raw = get_server(cfg).get("local_socks_port") or 1080
    return _as_int(raw, 1080)


def get_pac_listen_port(cfg: dict[str, Any] | None = None) -> int:
    raw = (os.environ.get("PAC_LISTEN_PORT") or "").strip()
    if raw:
        return _as_int(raw, 1089)
    src = cfg if cfg is not None else _runtime()
    return _as_int(src.get("pac_listen_port"), 1089)


def get_socks_scope(cfg: dict[str, Any] | None = None) -> str:
    src = cfg if cfg is not None else _runtime()
    if not infer_corporate(src):
        return "full"
    env = (os.environ.get("SOCKS_SCOPE") or "").strip().lower()
    if env:
        s = env
    else:
        s = str((src or {}).get("socks_scope") or "full").strip().lower()
    if s in ("full", "all", "system"):
        return "full"
    if s in ("github", "pac", "partial"):
        return "github"
    return "full"


def get_http_bridge_port(cfg: dict[str, Any] | None = None) -> int:
    raw = (os.environ.get("HTTP_BRIDGE_PORT") or "").strip()
    if raw:
        return _as_int(raw, 1088)
    src = cfg if cfg is not None else _runtime()
    return _as_int(src.get("http_bridge_port"), 1088)


def get_tun_enabled(cfg: dict[str, Any] | None = None) -> bool:
    raw = os.environ.get("TUN")
    if raw is not None and str(raw).strip() != "":
        return _truthy(raw)
    src = cfg if cfg is not None else _runtime()
    tun = src.get("tun") if isinstance(src.get("tun"), dict) else {}
    return _as_bool(tun.get("enabled"), True)


def get_kill_switch(cfg: dict[str, Any] | None = None) -> bool:
    raw = os.environ.get("KILL_SWITCH")
    if raw is not None and str(raw).strip() != "":
        return _truthy(raw)
    src = cfg if cfg is not None else _runtime()
    return _as_bool((src or {}).get("kill_switch"), True)


def get_tun_elevate(cfg: dict[str, Any] | None = None) -> bool:
    return True


def get_git_proxy_enabled(cfg: dict[str, Any] | None = None) -> bool:
    src = cfg if cfg is not None else _runtime()
    return _as_bool((src or {}).get("git_proxy"), False)


def get_docker_proxy_enabled(cfg: dict[str, Any] | None = None) -> bool:
    src = cfg if cfg is not None else _runtime()
    return _as_bool((src or {}).get("docker_proxy"), False)


def get_watchdog_enabled(cfg: dict[str, Any] | None = None) -> bool:
    raw = os.environ.get("WATCHDOG")
    if raw is not None and str(raw).strip() != "":
        return _truthy(raw)
    src = cfg if cfg is not None else _runtime()
    return _as_bool(src.get("watchdog"), True)


def get_watchdog_interval(cfg: dict[str, Any] | None = None) -> int:
    raw = (os.environ.get("WATCHDOG_INTERVAL") or "").strip()
    if raw.isdigit():
        return max(5, int(raw))
    src = cfg if cfg is not None else _runtime()
    return max(5, _as_int(src.get("watchdog_interval"), 15))


def get_watchdog_max_retries(cfg: dict[str, Any] | None = None) -> int:
    raw = (os.environ.get("WATCHDOG_MAX_RETRIES") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    src = cfg if cfg is not None else _runtime()
    return max(1, _as_int(src.get("watchdog_max_retries"), 5))


def resolve_corporate_proxy(cfg: dict[str, Any] | None = None) -> str:
    src = cfg if cfg is not None else _runtime()
    if not src:
        return ""
    use = infer_corporate(src) or _as_bool(src.get("use_proxy"), False)
    if not use:
        return ""
    raw = str(src.get("corporate_proxy") or "").strip()
    if not raw:
        raw = (os.environ.get("CORPORATE_PROXY") or "").strip()
    return raw.replace("http://", "").replace("https://", "").strip("/")


def get_sing_box_path(cfg: dict[str, Any] | None = None) -> str:
    """Return configured sing-box path, or empty for auto (tools/sing-box)."""
    from desktop.paths import data_root

    raw = (os.environ.get("SING_BOX_PATH") or "").strip()
    src = cfg if cfg is not None else _runtime()
    if not raw and src:
        tun = src.get("tun") or {}
        if isinstance(tun, dict):
            raw = str(tun.get("sing_box_path") or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = data_root() / path
    return str(path)


def get_reverse_ssh(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    src = cfg if cfg is not None else _runtime()
    rev = src.get("reverse_ssh") if isinstance(src.get("reverse_ssh"), dict) else {}
    return {
        "enabled": _as_bool(rev.get("enabled"), True),
        "vps_user": str(rev.get("vps_user") or "root").strip() or "root",
        "vps_port": max(1, min(65535, _as_int(rev.get("vps_port"), 22))),
        "listen_port": max(1, min(65535, _as_int(rev.get("listen_port"), 2222))),
        "local_port": max(1, min(65535, _as_int(rev.get("local_port"), 22))),
        "identity_file": str(rev.get("identity_file") or "").strip(),
    }


def get_reverse_ssh_enabled(cfg: dict[str, Any] | None = None) -> bool:
    raw = os.environ.get("REVERSE_SSH")
    if raw is not None and str(raw).strip() != "":
        return _truthy(raw)
    return bool(get_reverse_ssh(cfg).get("enabled"))


def get_vps_proxy_ports(cfg: dict[str, Any] | None = None) -> list[int]:
    """TCP ports on the VPS IP that must go via VLESS (office RST on :22)."""
    rev = get_reverse_ssh(cfg)
    ports = {22, int(rev.get("vps_port") or 22)}
    return sorted(p for p in ports if 1 <= p <= 65535)


def get_tun_mtu(cfg: dict[str, Any] | None = None) -> int:
    raw = (os.environ.get("TUN_MTU") or "").strip()
    if raw.isdigit():
        return max(1280, min(1500, int(raw)))
    src = cfg if cfg is not None else _runtime()
    if src:
        tun = src.get("tun") or {}
        if isinstance(tun, dict):
            return max(1280, min(1500, _as_int(tun.get("mtu"), 1400)))
    return 1400


def invoke_init(paths: Paths, log: LogFn = noop) -> None:
    paths.ensure_dirs()
    bundled = bundle_dir()
    example_cfg = bundled / "config" / "config.example.json"
    if not example_cfg.is_file():
        example_cfg = bundled / "config.example.json"

    created = False
    if not paths.config_path.is_file():
        if example_cfg.is_file():
            shutil.copyfile(example_cfg, paths.config_path)
        else:
            save_config(paths.config_path, default_config_template())
        created = True
        log("Created config.json")
    else:
        log("config.json already exists")

    cfg = load_config(paths.config_path)
    if paths.env_path.is_file():
        before = json.dumps(cfg, sort_keys=True)
        cfg = migrate_env_into_config(cfg, load_dotenv(paths.env_path))
        cfg = ensure_config_defaults(cfg)
        if json.dumps(cfg, sort_keys=True) != before:
            log("Migrated settings from .env → config.json")
        bak = paths.env_path.with_name(".env.migrated")
        try:
            if bak.is_file():
                bak.unlink()
            paths.env_path.rename(bak)
            log(f"Renamed {paths.env_path.name} -> {bak.name} (legacy, unused)")
        except OSError as exc:
            log(f"Could not rename .env: {exc}")
    save_config(paths.config_path, cfg)
    apply_config(paths.config_path, force=True)


def merge_settings_to_config(cfg: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(cfg)
    for k, v in updates.items():
        if k in ("ssh", "server", "tun", "transport", "reverse_ssh") and isinstance(v, dict):
            out.setdefault(k, {}).update(v)
        else:
            out[k] = v
    return ensure_config_defaults(out)
