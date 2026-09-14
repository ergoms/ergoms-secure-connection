"""Load/save config.json (single source of truth); migrate legacy .env; encrypt helpers."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from desktop.config.constants import (
    AWG_DEFAULT_ADDRESS,
    AWG_DEFAULT_MTU,
    AWG_DEFAULT_PORT,
    CORPORATE_BYPASS_PRESET,
    CORPORATE_PROXY_PRESET,
    REALITY_DEFAULT_SNI,
    STANDARD_BYPASS_PRESET,
)
from desktop.config.model import (
    AppConfig,
    as_bool as _as_bool,
    as_int as _as_int,
    default_config_template,
    ensure_config_defaults,
    normalize_dial,
    normalize_scope,
    truthy as _truthy,
)
from desktop.logutil import noop
from desktop.paths import Paths, bundle_dir


LogFn = Callable[[str], None]


AWG_CONF_NAME = "amneziawg.conf"


def awg_conf_path(config_path: Path) -> Path:
    """AmneziaWG lives next to config.json, never inside it."""
    return Path(config_path).parent / AWG_CONF_NAME


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


def amnezia_keys_ready(parsed: dict[str, Any] | None) -> bool:
    if not isinstance(parsed, dict):
        return False
    priv = str(parsed.get("private_key") or "").strip()
    pub = str(parsed.get("peer_public_key") or "").strip()
    return bool(priv and pub and "REPLACE" not in priv.upper())


def render_amnezia_conf(parsed: dict[str, Any], *, host: str = "") -> str:
    """Serialize parsed AWG fields back to a client .conf."""
    endpoint_host = str(host or parsed.get("host") or "").strip()
    port = max(1, min(65535, _as_int(parsed.get("port"), AWG_DEFAULT_PORT)))
    address = str(parsed.get("address") or AWG_DEFAULT_ADDRESS).strip() or AWG_DEFAULT_ADDRESS
    mtu = max(1280, min(1500, _as_int(parsed.get("mtu"), AWG_DEFAULT_MTU)))
    keepalive = max(0, min(600, _as_int(parsed.get("keepalive"), 25))) or 25
    lines = [
        "[Interface]",
        f"PrivateKey = {str(parsed.get('private_key') or '').strip()}",
        f"Address = {address}",
        f"MTU = {mtu}",
    ]
    for key, label in (
        ("jc", "Jc"),
        ("jmin", "Jmin"),
        ("jmax", "Jmax"),
        ("s1", "S1"),
        ("s2", "S2"),
    ):
        val = _as_int(parsed.get(key), 0)
        if val:
            lines.append(f"{label} = {val}")
    for key, label in (("h1", "H1"), ("h2", "H2"), ("h3", "H3"), ("h4", "H4")):
        val = str(parsed.get(key) or "").strip()
        if val:
            lines.append(f"{label} = {val}")
    lines.extend(["", "[Peer]", f"PublicKey = {str(parsed.get('peer_public_key') or '').strip()}"])
    psk = str(parsed.get("pre_shared_key") or "").strip()
    if psk:
        lines.append(f"PresharedKey = {psk}")
    if endpoint_host:
        lines.append(f"Endpoint = {endpoint_host}:{port}")
    lines.append("AllowedIPs = 0.0.0.0/0, ::/0")
    lines.append(f"PersistentKeepalive = {keepalive}")
    return "\n".join(lines) + "\n"


def apply_amnezia_to_config(cfg: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """Overlay parsed .conf onto the in-memory config (not written to JSON)."""
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


def overlay_amnezia_conf(cfg: dict[str, Any], conf_path: Path) -> dict[str, Any]:
    """Fill transport.amneziawg from amneziawg.conf when the file is valid."""
    if not conf_path.is_file():
        return cfg
    try:
        text = conf_path.read_text(encoding="utf-8-sig")
    except OSError:
        return cfg
    parsed = parse_amnezia_conf(text)
    if not amnezia_keys_ready(parsed):
        return cfg
    return apply_amnezia_to_config(cfg, parsed)


def strip_amneziawg_section(cfg: dict[str, Any]) -> dict[str, Any]:
    """config.json must not contain transport.amneziawg."""
    out = deepcopy(cfg) if isinstance(cfg, dict) else {}
    tr = out.get("transport")
    if isinstance(tr, dict):
        tr.pop("amneziawg", None)
    return out


def persistable_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Normalized JSON payload without AmneziaWG keys or leftover Hy2."""
    out = strip_amneziawg_section(ensure_config_defaults(cfg or {}))
    tr = out.get("transport")
    if isinstance(tr, dict):
        tr.pop("hysteria2", None)
        tr.pop("hy2_password", None)
    return out


def _write_awg_conf(conf_path: Path, text: str) -> None:
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    body = text if str(text).endswith("\n") else str(text) + "\n"
    conf_path.write_text(body, encoding="utf-8")
    try:
        os.chmod(conf_path, 0o600)
    except OSError:
        pass
    invalidate_config_cache()


def migrate_legacy_awg_json(cfg: dict[str, Any], conf_path: Path) -> bool:
    """If JSON still has AWG keys and .conf is missing, write the .conf once."""
    if conf_path.is_file():
        return False
    tr = cfg.get("transport") if isinstance(cfg.get("transport"), dict) else {}
    awg = tr.get("amneziawg") if isinstance(tr.get("amneziawg"), dict) else {}
    if not amnezia_keys_ready(awg) and isinstance(cfg.get("amneziawg"), dict):
        awg = cfg["amneziawg"]
    if not amnezia_keys_ready(awg):
        return False
    host = ""
    server = cfg.get("server")
    if isinstance(server, dict):
        host = str(server.get("host") or "").strip()
    _write_awg_conf(conf_path, render_amnezia_conf(awg, host=host))
    return True


def install_amnezia_conf(config_path: Path, text: str) -> dict[str, Any]:
    """Save a client .conf and switch dial to AmneziaWG. JSON keys stay empty."""
    parsed = parse_amnezia_conf(text)
    if not amnezia_keys_ready(parsed):
        raise ValueError("В .conf нет PrivateKey или PublicKey пира")
    path = Path(config_path)
    _write_awg_conf(awg_conf_path(path), text)
    if path.is_file():
        cfg = load_config(path, force=True)
    else:
        cfg = overlay_amnezia_conf(default_config_template(), awg_conf_path(path))
    tr = cfg.setdefault("transport", {})
    if isinstance(tr, dict):
        tr["dial"] = "amneziawg"
    save_config(path, cfg)
    return load_config(path, force=True)


def _nonempty(value: Any) -> bool:
    """False for None / blank strings / empty containers. 0 and False stay (real values)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def filled_str(value: Any) -> str:
    return str(value or "").strip()


def assign_filled(dst: dict[str, Any], key: str, incoming: Any) -> None:
    """Write incoming only when it has a value — never clear a filled key."""
    if isinstance(incoming, str):
        incoming = incoming.strip()
    if _nonempty(incoming):
        dst[key] = incoming


def merge_imported_config(
    base: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Fold a full or partial client JSON into one file. Empty values do not wipe keys."""
    src = deepcopy(incoming if isinstance(incoming, dict) else {})
    keys = set(src)
    fragment_keys = {
        "uuid",
        "public_key",
        "short_id",
        "server_name",
        "dial",
        "type",
    }
    src.pop("amneziawg", None)
    src.pop("hysteria2", None)
    if keys & fragment_keys and "transport" not in src and "server" not in src:
        wrap: dict[str, Any] = {}
        for key in ("uuid", "public_key", "short_id", "server_name", "port", "dial", "type"):
            if key in src:
                wrap[key] = src.pop(key)
        src = {"transport": wrap, **src}

    if base is None:
        return persistable_config(src)

    out = deepcopy(base)
    inc_tr = src.get("transport")
    if isinstance(inc_tr, dict):
        dst_tr = out.setdefault("transport", {})
        if not isinstance(dst_tr, dict):
            dst_tr = {}
            out["transport"] = dst_tr
        for key, val in inc_tr.items():
            if key in ("amneziawg", "hysteria2"):
                continue
            if _nonempty(val):
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
    """True if host + at least one transport (Reality / AWG) is filled."""
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
    awg = tr.get("amneziawg") if isinstance(tr.get("amneziawg"), dict) else {}
    return amnezia_keys_ready(awg)


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


_config_cache: tuple[str, float, float, dict[str, Any]] | None = None
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
        mtu = _as_int(env["TUN_MTU"], 1400)
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
    """Office defaults: VLESS via Squid. Does not clear filled proxy / bypass."""
    out = cfg
    out["corporate"] = True
    out["use_proxy"] = True
    tun = out.get("tun") if isinstance(out.get("tun"), dict) else {}
    if _as_bool(tun.get("enabled"), True) or _as_bool(out.get("kill_switch"), True):
        out["socks_scope"] = "full"
    else:
        out["socks_scope"] = "github"
    if not filled_str(out.get("corporate_proxy")) and CORPORATE_PROXY_PRESET:
        out["corporate_proxy"] = CORPORATE_PROXY_PRESET
    if not out.get("proxy_bypass"):
        out["proxy_bypass"] = list(CORPORATE_BYPASS_PRESET)
    out.setdefault("proxy_bypass_via", "direct")
    out["git_proxy"] = True
    out["docker_proxy"] = True
    tr = out.setdefault("transport", {})
    if isinstance(tr, dict):
        tr["dial"] = "vless-reality"
    return out


def apply_standard_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ordinary VPN: all traffic. Keeps filled proxy / bypass / keys."""
    out = cfg
    out["corporate"] = False
    out["use_proxy"] = False
    out["socks_scope"] = "full"
    if not out.get("proxy_bypass"):
        out["proxy_bypass"] = list(STANDARD_BYPASS_PRESET)
    out.setdefault("proxy_bypass_via", "direct")
    tr = out.setdefault("transport", {})
    if isinstance(tr, dict):
        tr["dial"] = "amneziawg"
    return out



def load_config(path: Path, *, force: bool = False) -> dict[str, Any]:
    global _config_cache
    if not path.is_file():
        raise FileNotFoundError(f"Missing config.json. Run init first: {path}")
    conf = awg_conf_path(path)
    json_mtime = _file_mtime(path)
    conf_mtime = _file_mtime(conf)
    try:
        cache_key = str(path.resolve())
    except OSError:
        cache_key = str(path)
    if (
        not force
        and _config_cache
        and _config_cache[0] == cache_key
        and _config_cache[1] == json_mtime
        and _config_cache[2] == conf_mtime
    ):
        return deepcopy(_config_cache[3])
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = ensure_config_defaults(raw if isinstance(raw, dict) else {})
    migrate_legacy_awg_json(cfg, conf)
    cfg = overlay_amnezia_conf(cfg, conf)
    _config_cache = (cache_key, json_mtime, _file_mtime(conf), cfg)
    return deepcopy(cfg)


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    """Write config.json without AmneziaWG. Empty incoming fields never erase filled keys."""
    payload = cfg if isinstance(cfg, dict) else {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict):
                payload = merge_imported_config(existing, payload)
            else:
                payload = ensure_config_defaults(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            payload = ensure_config_defaults(payload)
    else:
        payload = ensure_config_defaults(payload)
    payload = ensure_config_defaults(payload)
    migrate_legacy_awg_json(payload, awg_conf_path(path))
    persist = persistable_config(payload)
    path.write_text(
        json.dumps(persist, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    invalidate_config_cache()


def apply_config(path: Path, *, force: bool = False, env_path: Path | None = None) -> dict[str, Any]:
    """Load config.json into the runtime cache.

    env_path is ignored (kept for call-site compat). Legacy .env is merged only in init.
    """
    global _runtime_cfg
    del env_path  # legacy .env must not override config.json at runtime
    cfg = load_config(path, force=force)
    _runtime_cfg = cfg
    return deepcopy(cfg)



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
            tun["mtu"] = max(1280, min(1500, _as_int(raw, 1400)))
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
            tun["mtu"] = max(1280, min(1500, _as_int(value, 1400)))
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


def _app(cfg: dict[str, Any] | None = None) -> AppConfig:
    return AppConfig.from_dict(cfg if cfg is not None else _runtime())


def get_server(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    src = cfg if cfg is not None else _runtime()
    server = src.get("server") if isinstance(src.get("server"), dict) else None
    if server:
        return server
    return default_config_template()["server"]


def get_server_host(cfg: dict[str, Any] | None = None) -> str:
    return _app(cfg).server.host


def get_local_socks_port(cfg: dict[str, Any] | None = None) -> int:
    return _app(cfg).server.local_socks_port


def get_pac_listen_port(cfg: dict[str, Any] | None = None) -> int:
    return _app(cfg).pac_listen_port


def get_socks_scope(cfg: dict[str, Any] | None = None) -> str:
    src = cfg if cfg is not None else _runtime()
    if not infer_corporate(src):
        return "full"
    return normalize_scope(src.get("socks_scope"))


def get_http_bridge_port(cfg: dict[str, Any] | None = None) -> int:
    return _app(cfg).http_bridge_port


def get_tun_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).tun.enabled


def get_kill_switch(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).kill_switch


def get_tun_elevate(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).tun.elevate


def get_git_proxy_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).git_proxy


def get_docker_proxy_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).docker_proxy


def get_watchdog_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).watchdog


def get_watchdog_interval(cfg: dict[str, Any] | None = None) -> int:
    return _app(cfg).watchdog_interval


def get_watchdog_max_retries(cfg: dict[str, Any] | None = None) -> int:
    return _app(cfg).watchdog_max_retries


def resolve_corporate_proxy(cfg: dict[str, Any] | None = None) -> str:
    src = cfg if cfg is not None else _runtime()
    if not src:
        return ""
    use = infer_corporate(src) or _as_bool(src.get("use_proxy"), False)
    if not use:
        return ""
    raw = str(src.get("corporate_proxy") or "").strip()
    return raw.replace("http://", "").replace("https://", "").strip("/")


def get_sing_box_path(cfg: dict[str, Any] | None = None) -> str:
    """Return configured sing-box path, or empty for auto (tools/sing-box)."""
    from desktop.paths import data_root

    raw = _app(cfg).tun.sing_box_path
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = data_root() / path
    return str(path)


def get_reverse_ssh(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    rev = _app(cfg).reverse_ssh
    return {
        "enabled": rev.enabled,
        "vps_user": rev.vps_user,
        "vps_port": rev.vps_port,
        "listen_port": rev.listen_port,
        "local_port": rev.local_port,
        "identity_file": rev.identity_file,
    }


def get_reverse_ssh_enabled(cfg: dict[str, Any] | None = None) -> bool:
    return _app(cfg).reverse_ssh.enabled


def get_vps_proxy_ports(cfg: dict[str, Any] | None = None) -> list[int]:
    """SSH ports on the VPS IP.

    Office TUN sends them via VLESS (Squid RST on :22). Home leaves them on the
    underlay so ``ssh user@vps`` works while VPN is up. Reverse SSH always
    dials the same ports through local SOCKS → VLESS.
    """
    rev = _app(cfg).reverse_ssh
    ports = {22, rev.vps_port}
    return sorted(p for p in ports if 1 <= p <= 65535)


def get_tun_mtu(cfg: dict[str, Any] | None = None) -> int:
    return _app(cfg).tun.mtu


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
