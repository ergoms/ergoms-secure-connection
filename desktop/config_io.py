"""Load/save config.json (single source of truth); migrate legacy .env; encrypt helpers."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from desktop.paths import Paths, bundle_dir


LogFn = Callable[[str], None]

CORPORATE_PROXY_PRESET = "10.16.0.8:3128"
CORPORATE_BYPASS_PRESET = ["*.tu-bryansk.ru", "*.local", "*.lan"]
STANDARD_BYPASS_PRESET = ["*.local", "*.lan"]

# Legacy .env keys → config.json (migration only)
_ENV_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})


def _noop(msg: str) -> None:
    pass


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
            "enabled": False,
            "elevate": True,
            "sing_box_path": "",
            "mtu": 1500,
        },
        "transport": {
            "type": "vless-reality",
            "uuid": "",
            "public_key": "",
            "short_id": "",
            "server_name": "www.cloudflare.com",
            "port": 443,
        },
        "reverse_ssh": {
            "enabled": False,
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
    if "TUN" in env:
        tun["enabled"] = _as_bool(env["TUN"], False)
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
            rev["enabled"] = _as_bool(env["REVERSE_SSH"], False)
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
    rev = out.setdefault("reverse_ssh", {})
    if isinstance(rev, dict):
        rev["enabled"] = False
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
    tun.setdefault("enabled", False)
    tun.setdefault("elevate", True)
    tun.setdefault("sing_box_path", "")
    tun.setdefault("mtu", 1500)
    tun["enabled"] = _as_bool(tun.get("enabled"), False)
    tun["elevate"] = _as_bool(tun.get("elevate"), True)

    transport = out.setdefault("transport", {})
    if not isinstance(transport, dict):
        transport = {}
        out["transport"] = transport
    transport.setdefault("type", "vless-reality")
    transport.setdefault("uuid", "")
    transport.setdefault("public_key", "")
    transport.setdefault("short_id", "")
    transport.setdefault("server_name", "www.cloudflare.com")
    transport.setdefault("port", 443)

    rev = out.setdefault("reverse_ssh", {})
    if not isinstance(rev, dict):
        rev = {}
        out["reverse_ssh"] = rev
    rev.setdefault("enabled", False)
    rev.setdefault("vps_user", "root")
    rev.setdefault("vps_port", 22)
    rev.setdefault("listen_port", 2222)
    rev.setdefault("local_port", 22)
    rev.setdefault("identity_file", "")
    rev["enabled"] = _as_bool(rev.get("enabled"), False)
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
    os.environ["TUN"] = "1" if _as_bool(tun.get("enabled"), False) else "0"
    os.environ["TUN_ELEVATE"] = "1" if _as_bool(tun.get("elevate"), True) else "0"
    os.environ["WATCHDOG"] = "1" if _as_bool(cfg.get("watchdog"), True) else "0"
    os.environ["WATCHDOG_INTERVAL"] = str(cfg.get("watchdog_interval") or 15)
    os.environ["WATCHDOG_MAX_RETRIES"] = str(cfg.get("watchdog_max_retries") or 5)
    corp = resolve_corporate_proxy(cfg)
    if corp:
        os.environ["CORPORATE_PROXY"] = corp
    else:
        os.environ.pop("CORPORATE_PROXY", None)


# Back-compat aliases
def apply_dotenv(path: Path, *, force: bool = False) -> dict[str, str]:
    """Deprecated: prefer apply_config. Still syncs runtime from config (+ legacy .env)."""
    root = path.parent if path.name == ".env" else path.parent
    cfg_path = root / "config.json"
    if cfg_path.is_file():
        apply_config(cfg_path, force=force, env_path=path if path.is_file() else None)
    elif path.is_file():
        data = load_dotenv(path)
        for k, v in data.items():
            os.environ[k] = v
        return data
    return {
        "SOCKS_SCOPE": os.environ.get("SOCKS_SCOPE", "full"),
        "TUN": os.environ.get("TUN", "0"),
        "TUN_ELEVATE": os.environ.get("TUN_ELEVATE", "1"),
    }


def save_dotenv(path: Path, values: dict[str, str], preserve_comments: bool = True) -> None:
    """Deprecated: write runtime switches into config.json instead of .env."""
    del preserve_comments  # unused
    cfg_path = path.parent / "config.json"
    if cfg_path.is_file():
        cfg = load_config(cfg_path)
    else:
        cfg = default_config_template()
    cfg = migrate_env_into_config(cfg, values)
    save_config(cfg_path, cfg)
    apply_config(cfg_path, force=True)


def update_env_key(path: Path, key: str, value: str) -> None:
    """Deprecated name: set one runtime key in config.json."""
    update_config_key(path.parent / "config.json" if path.name == ".env" else path, key, value)


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
        "SING_BOX_PATH": ("tun.sing_box_path", value),
        "TUN_MTU": ("tun.mtu", value),
        "REVERSE_SSH": ("reverse_ssh.enabled", value),
    }
    if key_u in mapping:
        target, raw = mapping[key_u]
        if target == "tun.enabled":
            tun["enabled"] = _as_bool(raw, False)
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
            tun[sub] = _as_bool(value, sub == "elevate")
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
    return _as_bool(tun.get("enabled"), False)


def get_tun_elevate(cfg: dict[str, Any] | None = None) -> bool:
    return True


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
        "enabled": _as_bool(rev.get("enabled"), False),
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
            return max(1280, min(1500, _as_int(tun.get("mtu"), 1500)))
    return 1500


def invoke_init(paths: Paths, log: LogFn = _noop) -> None:
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
