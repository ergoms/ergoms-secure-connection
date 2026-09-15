"""Windows system proxy (PAC) via HKCU Internet Settings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from desktop.logutil import noop

LogFn = Callable[[str], None]


def _is_windows() -> bool:
    return sys.platform == "win32"


def notify_proxy_change() -> None:
    if not _is_windows():
        return
    try:
        import ctypes

        InternetSetOption = ctypes.windll.wininet.InternetSetOptionW  # type: ignore[attr-defined]
        InternetSetOption(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
        InternetSetOption(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH
    except Exception:  # noqa: BLE001
        pass


def _reg_key():
    import winreg

    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    )


def _get_reg_int(key, name: str, default: int = 0) -> int:
    import winreg

    try:
        val, _ = winreg.QueryValueEx(key, name)
        return int(val)
    except OSError:
        return default


def _get_reg_str(key, name: str, default: str = "") -> str:
    import winreg

    try:
        val, _ = winreg.QueryValueEx(key, name)
        return str(val) if val is not None else default
    except OSError:
        return default


def _set_reg_int(key, name: str, value: int) -> None:
    import winreg

    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))


def _set_reg_str(key, name: str, value: str) -> None:
    import winreg

    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_reg(key, name: str) -> None:
    import winreg

    try:
        winreg.DeleteValue(key, name)
    except OSError:
        pass


def backup_win_proxy(backup_path: Path) -> None:
    if not _is_windows() or backup_path.is_file():
        return
    import winreg

    with _reg_key() as key:
        data = {
            "ProxyEnable": _get_reg_int(key, "ProxyEnable", 0),
            "ProxyServer": _get_reg_str(key, "ProxyServer", ""),
            "ProxyOverride": _get_reg_str(key, "ProxyOverride", ""),
            "AutoConfigURL": _get_reg_str(key, "AutoConfigURL", ""),
            "AutoDetect": _get_reg_int(key, "AutoDetect", 0),
        }
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _is_our_pac(url: str) -> bool:
    text = (url or "").strip().lower()
    if "127.0.0.1" not in text and "localhost" not in text:
        return False
    return "proxy.pac" in text or ":1089" in text


def _is_our_static(server: str) -> bool:
    text = (server or "").strip().lower().replace("http://", "")
    return text.startswith("127.0.0.1:") or text.startswith("localhost:")


def proxy_override_list(bypass_hosts: list[str] | None = None) -> str:
    """WinINET ProxyOverride: LAN + config bypass, no PAC / no DNS."""
    parts = [
        "<local>",
        "127.0.0.1",
        "localhost",
        "10.*",
        "192.168.*",
        "169.254.*",
    ]
    seen = {p.lower() for p in parts}
    from desktop.route_tokens import is_host_pattern

    for raw in bypass_hosts or []:
        host = str(raw or "").strip()
        if not host or not is_host_pattern(host):
            continue
        key = host.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(host)
    return ";".join(parts)


def current_auto_config_url() -> str:
    if not _is_windows():
        return ""
    with _reg_key() as key:
        return _get_reg_str(key, "AutoConfigURL", "").strip()


def current_proxy_server() -> str:
    if not _is_windows():
        return ""
    with _reg_key() as key:
        return _get_reg_str(key, "ProxyServer", "").strip()


def pac_url_active(url: str) -> bool:
    """True if Internet Settings already point at this PAC and not a static proxy."""
    want = (url or "").strip()
    if not want or not _is_windows():
        return False
    with _reg_key() as key:
        current = _get_reg_str(key, "AutoConfigURL", "").strip()
        proxy_on = _get_reg_int(key, "ProxyEnable", 0)
        auto = _get_reg_int(key, "AutoDetect", 0)
    return current == want and proxy_on == 0 and auto == 0


def static_proxy_active(http_port: int, override: str) -> bool:
    if not _is_windows():
        return False
    want = f"127.0.0.1:{int(http_port)}"
    with _reg_key() as key:
        proxy_on = _get_reg_int(key, "ProxyEnable", 0)
        auto = _get_reg_int(key, "AutoDetect", 0)
        server = _get_reg_str(key, "ProxyServer", "").strip()
        pac = _get_reg_str(key, "AutoConfigURL", "").strip()
        have_override = _get_reg_str(key, "ProxyOverride", "").strip()
    return (
        proxy_on == 1
        and auto == 0
        and not pac
        and server.lower() == want
        and have_override == override
    )


def wininet_is_direct() -> bool:
    if not _is_windows():
        return True
    with _reg_key() as key:
        proxy_on = _get_reg_int(key, "ProxyEnable", 0)
        auto = _get_reg_int(key, "AutoDetect", 0)
        pac = _get_reg_str(key, "AutoConfigURL", "").strip()
    return proxy_on == 0 and auto == 0 and not pac


def force_wininet_direct(backup_path: Path, log: LogFn = noop) -> None:
    """Clear PAC/proxy so Chrome follows TUN routes. Keep backup for explicit off."""
    if not _is_windows():
        return
    backup_win_proxy(backup_path)
    if wininet_is_direct():
        return
    with _reg_key() as key:
        _set_reg_int(key, "ProxyEnable", 0)
        _set_reg_int(key, "AutoDetect", 0)
        _delete_reg(key, "AutoConfigURL")
        if _is_our_static(_get_reg_str(key, "ProxyServer", "")):
            _delete_reg(key, "ProxyServer")
    notify_proxy_change()
    log("Windows proxy: DIRECT — браузер через TUN")


def restore_win_proxy(backup_path: Path, log: LogFn = noop) -> None:
    if not _is_windows():
        return
    import winreg

    with _reg_key() as key:
        current_pac = _get_reg_str(key, "AutoConfigURL", "")
        if backup_path.is_file():
            b: dict[str, Any] = json.loads(backup_path.read_text(encoding="utf-8-sig"))
            _set_reg_int(key, "ProxyEnable", int(b.get("ProxyEnable") or 0))
            if b.get("ProxyServer") is not None:
                _set_reg_str(key, "ProxyServer", str(b.get("ProxyServer") or ""))
            if b.get("ProxyOverride") is not None:
                _set_reg_str(key, "ProxyOverride", str(b.get("ProxyOverride") or ""))
            if "AutoDetect" in b:
                _set_reg_int(key, "AutoDetect", int(b.get("AutoDetect") or 0))
            ac = b.get("AutoConfigURL") or ""
            if ac and not _is_our_pac(str(ac)):
                _set_reg_str(key, "AutoConfigURL", str(ac))
            else:
                _delete_reg(key, "AutoConfigURL")
            backup_path.unlink(missing_ok=True)
            notify_proxy_change()
            log("Windows proxy restored from backup")
            return
        server = _get_reg_str(key, "ProxyServer", "")
        if _is_our_pac(current_pac) or _is_our_static(server):
            _delete_reg(key, "AutoConfigURL")
            _set_reg_int(key, "ProxyEnable", 0)
            if _is_our_static(server):
                _delete_reg(key, "ProxyServer")
            notify_proxy_change()
            log("Windows PAC/прокси снят")
            return


def enable_browser_static_proxy(
    http_port: int,
    bypass_hosts: list[str] | None,
    backup_path: Path,
    log: LogFn = noop,
) -> None:
    """Office: fixed 127.0.0.1:port — no PAC file, Chrome does not wait."""
    if not _is_windows():
        log("Browser proxy: Windows only")
        return
    server = f"127.0.0.1:{int(http_port)}"
    override = proxy_override_list(bypass_hosts)
    backup_win_proxy(backup_path)
    if static_proxy_active(http_port, override):
        return
    with _reg_key() as key:
        _set_reg_int(key, "ProxyEnable", 1)
        _set_reg_int(key, "AutoDetect", 0)
        _delete_reg(key, "AutoConfigURL")
        _set_reg_str(key, "ProxyServer", server)
        _set_reg_str(key, "ProxyOverride", override)
    notify_proxy_change()
    log(f"офис: системный прокси {server} (без PAC), bypass={override}")


def enable_browser_pac(
    http_port: int,
    scope: str,
    bypass_count: int,
    backup_path: Path,
    log: LogFn = noop,
    *,
    pac_url: str | None = None,
) -> None:
    if not _is_windows():
        log("Browser PAC: Windows only")
        return
    url = (pac_url or "").strip() or f"http://127.0.0.1:{http_port}/proxy.pac"
    backup_win_proxy(backup_path)
    if pac_url_active(url):
        return
    with _reg_key() as key:
        _set_reg_int(key, "ProxyEnable", 0)
        _set_reg_int(key, "AutoDetect", 0)
        _set_reg_str(key, "AutoConfigURL", url)
    notify_proxy_change()
    if scope == "full":
        log(f"Browser PAC FULL = {url} (VPS except proxy_bypass={bypass_count})")
    else:
        log(f"Browser PAC = {url} (GitHub via VPS; bypass={bypass_count})")
    log("Restart Edge/Chrome tabs if sites still look cached/blocked")


def disable_browser_proxy(backup_path: Path, log: LogFn = noop) -> None:
    restore_win_proxy(backup_path, log=log)
