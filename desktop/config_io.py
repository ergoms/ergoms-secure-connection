"""Load/save .env and config.json; init from bundled examples."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from desktop.paths import Paths, bundle_dir


LogFn = Callable[[str], None]

# Keys persisted in root .env (runtime switches)
ENV_KEYS = [
    "MODE",
    "SOCKS_SCOPE",
    "HTTP_BRIDGE_PORT",
    "OPS_CONTENT_SECRET",
    "CORPORATE_PROXY",
    "TUN",
    "TUN_ELEVATE",
]


def _noop(msg: str) -> None:
    pass


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on", "full")


def load_dotenv(path: Path) -> dict[str, str]:
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


def apply_dotenv(path: Path) -> dict[str, str]:
    """Load .env into process env (source of truth for MODE / TUN etc.)."""
    data = load_dotenv(path)
    for k, v in data.items():
        os.environ[k] = v
    return data


def _default_env_text(values: dict[str, str]) -> str:
    tun = values.get("TUN", "0") or "0"
    elevate = values.get("TUN_ELEVATE", "1") or "1"
    lines = [
        "# Client mode: socks | vps",
        f"MODE={values.get('MODE', 'socks') or 'socks'}",
        "",
        "# full | github",
        f"SOCKS_SCOPE={values.get('SOCKS_SCOPE', 'full') or 'full'}",
        "",
        "# TUN over SOCKS (sing-box). 1 = auto-start TUN after tunnel on",
        f"TUN={tun}",
        "# Request UAC when starting sing-box TUN",
        f"TUN_ELEVATE={elevate}",
        "",
    ]
    if values.get("HTTP_BRIDGE_PORT"):
        lines.append(f"HTTP_BRIDGE_PORT={values['HTTP_BRIDGE_PORT']}")
    else:
        lines.append("# HTTP_BRIDGE_PORT=1088")
    if values.get("OPS_CONTENT_SECRET"):
        lines.append(f"OPS_CONTENT_SECRET={values['OPS_CONTENT_SECRET']}")
    else:
        lines.append("# OPS_CONTENT_SECRET=")
    if values.get("CORPORATE_PROXY"):
        lines.append(f"CORPORATE_PROXY={values['CORPORATE_PROXY']}")
    else:
        lines.append("# CORPORATE_PROXY=10.16.0.8:3128  (overrides config.json if set)")
    lines.append("")
    return "\n".join(lines)


def save_dotenv(path: Path, values: dict[str, str], preserve_comments: bool = True) -> None:
    """Write env keys; keep comment/blank structure when file exists."""
    # Normalize known keys
    cleaned = {k: (values.get(k) or "").strip() for k in ENV_KEYS if k in values}
    # Always persist TUN defaults if provided in values or missing later
    for k in ENV_KEYS:
        if k in values:
            cleaned[k] = (values.get(k) or "").strip()

    existing_lines: list[str] = []
    if preserve_comments and path.is_file():
        existing_lines = path.read_text(encoding="utf-8-sig").splitlines()

    if not existing_lines:
        path.write_text(_default_env_text(cleaned), encoding="utf-8")
        return

    seen: set[str] = set()
    out: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in cleaned:
            out.append(f"{key}={cleaned[key]}")
            seen.add(key)
        else:
            out.append(line)

    # Append missing keys that we care about
    extras: list[str] = []
    for key in ENV_KEYS:
        if key not in cleaned or key in seen:
            continue
        # Skip empty optional secrets/ports unless TUN flags
        if key in ("OPS_CONTENT_SECRET", "CORPORATE_PROXY", "HTTP_BRIDGE_PORT") and not cleaned[key]:
            continue
        extras.append(f"{key}={cleaned[key]}")
        seen.add(key)
    if extras:
        if out and out[-1].strip():
            out.append("")
        if "TUN" in cleaned and "TUN" not in {
            ln.split("=", 1)[0].strip()
            for ln in existing_lines
            if ln.strip() and not ln.strip().startswith("#") and "=" in ln
        }:
            out.append("# TUN over SOCKS (sing-box): 0=off, 1=auto after tunnel on")
        out.extend(extras)

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def update_env_key(path: Path, key: str, value: str) -> None:
    """Set one .env key and reload into process env."""
    data = load_dotenv(path)
    data[key] = value
    # Ensure TUN keys exist when touching TUN
    if key == "TUN" and "TUN_ELEVATE" not in data:
        data["TUN_ELEVATE"] = "1"
    save_dotenv(path, data)
    apply_dotenv(path)


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing config.json. Run init first: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    return ensure_config_defaults(cfg)


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(ensure_config_defaults(cfg), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def ensure_config_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Fill missing sections without wiping user values."""
    out = deepcopy(cfg) if cfg else {}
    out.setdefault("corporate_proxy", "10.16.0.8:3128")
    ssh = out.setdefault("ssh", {})
    if not isinstance(ssh, dict):
        ssh = {}
        out["ssh"] = ssh
    ssh.setdefault("host", "YOUR_VPS_IP_OR_HOSTNAME")
    ssh.setdefault("user", "root")
    ssh.setdefault("port", 443)
    ssh.setdefault("identity_file", "")
    ssh.setdefault("local_socks_port", 1080)
    out.setdefault("worker_base_url", "")
    out.setdefault("blocked_hosts", default_config_template()["blocked_hosts"])
    out.setdefault("proxy_bypass", ["*.tu-bryansk.ru", "*.local", "*.lan"])
    out.setdefault("proxy_bypass_via", "direct")
    tun = out.setdefault("tun", {})
    if not isinstance(tun, dict):
        tun = {}
        out["tun"] = tun
    tun.setdefault("sing_box_path", "")
    return out


def get_mode() -> str:
    return (os.environ.get("MODE") or "socks").strip().lower()


def get_socks_scope() -> str:
    s = (os.environ.get("SOCKS_SCOPE") or "full").strip().lower()
    if s in ("full", "all", "system"):
        return "full"
    if s in ("github", "pac", "partial"):
        return "github"
    return "full"


def get_http_bridge_port() -> int:
    raw = (os.environ.get("HTTP_BRIDGE_PORT") or "").strip()
    if raw:
        return int(raw)
    return 1088


def get_tun_enabled() -> bool:
    """TUN flag from .env (applied to process env)."""
    return _truthy(os.environ.get("TUN"))


def get_tun_elevate() -> bool:
    raw = os.environ.get("TUN_ELEVATE")
    if raw is None or raw.strip() == "":
        return True
    return _truthy(raw)


def resolve_corporate_proxy(cfg: dict[str, Any] | None = None) -> str:
    """CORPORATE_PROXY from .env overrides config.json."""
    env_p = (os.environ.get("CORPORATE_PROXY") or "").strip()
    if env_p:
        return env_p.replace("http://", "").replace("https://", "").strip("/")
    if cfg:
        return str(cfg.get("corporate_proxy") or "10.16.0.8:3128")
    return "10.16.0.8:3128"


def get_sing_box_path(cfg: dict[str, Any] | None = None) -> str:
    env_p = (os.environ.get("SING_BOX_PATH") or "").strip()
    if env_p:
        return env_p
    if cfg:
        tun = cfg.get("tun") or {}
        if isinstance(tun, dict):
            return str(tun.get("sing_box_path") or "").strip()
    return ""


def invoke_init(paths: Paths, log: LogFn = _noop) -> None:
    paths.ensure_dirs()
    bundled = bundle_dir()
    example_cfg = bundled / "config" / "config.example.json"
    example_env = bundled / "config" / ".env.example"
    if not example_cfg.is_file():
        example_cfg = bundled / "config.example.json"
    if not example_env.is_file():
        example_env = bundled / ".env.example"

    if not paths.config_path.is_file():
        if not example_cfg.is_file():
            raise FileNotFoundError(f"Missing example config: {example_cfg}")
        shutil.copyfile(example_cfg, paths.config_path)
        log("Created config.json")
    else:
        # Ensure tun section exists in older configs
        try:
            cfg = load_config(paths.config_path)
            save_config(paths.config_path, cfg)
        except Exception:  # noqa: BLE001
            pass
        log("config.json already exists")

    if not paths.env_path.is_file() and example_env.is_file():
        shutil.copyfile(example_env, paths.env_path)
        log("Created .env from example")
    elif paths.env_path.is_file():
        # Merge missing TUN keys into existing .env
        data = load_dotenv(paths.env_path)
        changed = False
        if "TUN" not in data:
            data["TUN"] = "0"
            changed = True
        if "TUN_ELEVATE" not in data:
            data["TUN_ELEVATE"] = "1"
            changed = True
        if changed:
            save_dotenv(paths.env_path, data)
            log("Updated .env with TUN keys")

    apply_dotenv(paths.env_path)


def default_config_template() -> dict[str, Any]:
    return {
        "corporate_proxy": "10.16.0.8:3128",
        "ssh": {
            "host": "YOUR_VPS_IP_OR_HOSTNAME",
            "user": "root",
            "port": 443,
            "identity_file": "",
            "local_socks_port": 1080,
        },
        "worker_base_url": "",
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
        "proxy_bypass": ["*.tu-bryansk.ru", "*.local", "*.lan"],
        "proxy_bypass_via": "direct",
        "tun": {
            "sing_box_path": "",
        },
    }


def merge_settings_to_config(cfg: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(cfg)
    for k, v in updates.items():
        if k in ("ssh", "tun") and isinstance(v, dict):
            out.setdefault(k, {}).update(v)
        else:
            out[k] = v
    return ensure_config_defaults(out)
