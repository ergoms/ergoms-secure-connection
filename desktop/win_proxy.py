"""Windows system proxy (PAC) via HKCU Internet Settings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


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


def restore_win_proxy(backup_path: Path, log: LogFn = _noop) -> None:
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
        if _is_our_pac(current_pac):
            _delete_reg(key, "AutoConfigURL")
            _set_reg_int(key, "ProxyEnable", 0)
            notify_proxy_change()
            log("Windows PAC снят")
            return


def enable_browser_pac(
    http_port: int,
    scope: str,
    bypass_count: int,
    backup_path: Path,
    log: LogFn = _noop,
    *,
    pac_url: str | None = None,
) -> None:
    if not _is_windows():
        log("Browser PAC: Windows only")
        return
    url = (pac_url or "").strip() or f"http://127.0.0.1:{http_port}/proxy.pac"
    backup_win_proxy(backup_path)
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


def disable_browser_proxy(backup_path: Path, log: LogFn = _noop) -> None:
    restore_win_proxy(backup_path, log=log)
