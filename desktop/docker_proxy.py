"""Point Docker Desktop + CLI at the local HTTP bridge (Windows).

Docker Desktop in WSL cannot use the Windows PAC file: from the VM,
127.0.0.1 is the guest. System-proxy mode also often omits HTTPS, so
`docker pull` goes DIRECT through the office network and dies.

Manual proxy via host.docker.internal is what Docker documents for a
proxy that listens on the Windows host.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]

_PROXY_STORE_KEYS = (
    "ProxyHTTPMode",
    "OverrideProxyHTTP",
    "OverrideProxyHTTPS",
    "OverrideProxyExclude",
    "ContainersProxyHTTPMode",
    "ContainersOverrideProxyHTTP",
    "ContainersOverrideProxyHTTPS",
    "ContainersOverrideProxyExclude",
)


def _noop(msg: str) -> None:
    pass


def _settings_store_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Docker" / "settings-store.json"


def _cli_config_path() -> Path:
    return Path.home() / ".docker" / "config.json"


def _noproxy() -> str:
    return ",".join(
        [
            "localhost",
            "127.0.0.1",
            "::1",
            "host.docker.internal",
            "192.168.65.0/24",
            "192.168.65.254",
            "10.0.0.0/8",
            "172.16.0.0/12",
        ]
    )


def _container_proxy_url(http_port: int) -> str:
    """Host-gateway IP as seen from Linux containers (loopback forwarding)."""
    host = (os.environ.get("OPS_CONTENT_DOCKER_HOST_IP") or "").strip()
    if host.startswith(("10.", "100.")):
        host = ""
    if not host:
        host = "192.168.65.254"
    return f"http://{host}:{int(http_port)}"


def _host_proxy_url(http_port: int) -> str:
    """Windows-side Docker Desktop httpproxy dials this with Win32 connectex."""
    return f"http://127.0.0.1:{int(http_port)}"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _is_our_cli_proxy(default: dict[str, Any], http_port: int) -> bool:
    port = str(int(http_port))
    for key in ("httpProxy", "httpsProxy"):
        val = str(default.get(key) or "")
        if f":{port}" not in val:
            continue
        if "host.docker.internal" in val or "127.0.0.1" in val or "192.168.65." in val:
            return True
    return False


def enable_docker_desktop_proxy(
    http_port: int,
    backup_path: Path,
    *,
    log: LogFn = _noop,
) -> None:
    """Switch Docker Desktop + ~/.docker/config.json to the local HTTP bridge."""
    if sys.platform != "win32":
        return
    port = int(http_port)
    # Both URLs are dialed from Windows (Docker Desktop httpproxy / connectex).
    # 192.168.65.254 is only reachable from Linux containers, not from Win32.
    host_proxy = _host_proxy_url(port)
    noproxy = _noproxy()

    store_path = _settings_store_path()
    cli_path = _cli_config_path()
    store = _read_json(store_path)
    cli = _read_json(cli_path)

    if not backup_path.is_file():
        _write_json(
            backup_path,
            {
                "settings_store": {k: store[k] for k in _PROXY_STORE_KEYS if k in store},
                "cli_config_proxies": cli.get("proxies") if cli else None,
                "had_cli_config": cli_path.is_file(),
            },
        )

    if store_path.is_file() and store:
        store["ProxyHTTPMode"] = "manual"
        store["OverrideProxyHTTP"] = host_proxy
        store["OverrideProxyHTTPS"] = host_proxy
        store["OverrideProxyExclude"] = noproxy
        store["ContainersProxyHTTPMode"] = "manual"
        store["ContainersOverrideProxyHTTP"] = host_proxy
        store["ContainersOverrideProxyHTTPS"] = host_proxy
        store["ContainersOverrideProxyExclude"] = noproxy
        _write_json(store_path, store)
        log(
            f"Docker Desktop proxy = {host_proxy} (host + Linux engine). "
            "Если docker pull уже открыт — перезапустите Docker Desktop"
        )
    elif not store_path.is_file():
        log("Docker Desktop settings-store.json нет — только CLI proxies")

    if not cli and not cli_path.is_file() and not Path.home().joinpath(".docker").is_dir():
        return
    ctr_proxy = _container_proxy_url(port)
    proxies = cli.setdefault("proxies", {})
    if not isinstance(proxies, dict):
        proxies = {}
        cli["proxies"] = proxies
    proxies["default"] = {
        "httpProxy": ctr_proxy,
        "httpsProxy": ctr_proxy,
        "noProxy": noproxy,
    }
    _write_json(cli_path, cli)
    log(f"docker config.json proxies.default = {ctr_proxy}")


def disable_docker_desktop_proxy(
    backup_path: Path,
    *,
    http_port: int = 1088,
    log: LogFn = _noop,
) -> None:
    """Restore Docker Desktop / CLI proxy from the on-enable backup."""
    if sys.platform != "win32":
        return
    if not backup_path.is_file():
        return
    backup = _read_json(backup_path)
    _restore_settings_store(backup, log=log)
    _restore_cli_config(backup, http_port=int(http_port), log=log)
    backup_path.unlink(missing_ok=True)


def _restore_settings_store(backup: dict[str, Any], *, log: LogFn) -> None:
    store_path = _settings_store_path()
    if not store_path.is_file():
        return
    data = _read_json(store_path)
    if not data:
        return
    snap = backup.get("settings_store")
    if isinstance(snap, dict):
        for key in _PROXY_STORE_KEYS:
            if key in snap:
                data[key] = snap[key]
            else:
                data.pop(key, None)
    else:
        for key in _PROXY_STORE_KEYS:
            data.pop(key, None)
        data["ProxyHTTPMode"] = "system"
        data["ContainersProxyHTTPMode"] = "system"
    _write_json(store_path, data)
    log("Docker Desktop proxy restored")


def _restore_cli_config(
    backup: dict[str, Any], *, http_port: int, log: LogFn
) -> None:
    path = _cli_config_path()
    data = _read_json(path)
    if not data and not path.is_file():
        return
    snap = backup.get("cli_config_proxies") if backup else None
    had_backup = bool(backup) and "cli_config_proxies" in backup
    default = None
    proxies = data.get("proxies")
    if isinstance(proxies, dict):
        default = proxies.get("default")
    ours = isinstance(default, dict) and _is_our_cli_proxy(default, http_port)
    if not had_backup and not ours:
        return
    if had_backup:
        if snap:
            data["proxies"] = snap
        else:
            data.pop("proxies", None)
    elif ours and isinstance(proxies, dict):
        proxies.pop("default", None)
        if not proxies:
            data.pop("proxies", None)
    if data:
        _write_json(path, data)
    elif path.is_file() and not backup.get("had_cli_config"):
        path.unlink(missing_ok=True)
    else:
        _write_json(path, data)
    log("docker config.json proxies restored")
