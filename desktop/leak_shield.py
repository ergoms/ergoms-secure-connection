"""Close DNS/WebRTC leaks that OS routes do not see (Windows).

While TUN or the kill switch is up: disable Windows and browser DoH, pin
WebRTC to the public (TUN) interface. Restored only on explicit off.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Protocol

from desktop.kill_switch import _load_state, _save_state
from desktop.logutil import noop

LogFn = Callable[[str], None]

STATE_KEY = "leak_shield"
HIVE_HKCU = "HKCU"
HIVE_HKLM = "HKLM"
TYPE_SZ = "sz"
TYPE_DWORD = "dword"

_BROWSER_KEYS: tuple[tuple[str, str, str, Any, str], ...] = (
    (
        HIVE_HKCU,
        r"Software\Policies\Google\Chrome",
        "DnsOverHttpsMode",
        "off",
        TYPE_SZ,
    ),
    (
        HIVE_HKCU,
        r"Software\Policies\Google\Chrome",
        "WebRtcIPHandling",
        "default_public_interface_only",
        TYPE_SZ,
    ),
    (
        HIVE_HKCU,
        r"Software\Policies\Google\Chrome",
        "QuicAllowed",
        0,
        TYPE_DWORD,
    ),
    (
        HIVE_HKCU,
        r"Software\Policies\Microsoft\Edge",
        "DnsOverHttpsMode",
        "off",
        TYPE_SZ,
    ),
    (
        HIVE_HKCU,
        r"Software\Policies\Microsoft\Edge",
        "WebRtcIPHandling",
        "default_public_interface_only",
        TYPE_SZ,
    ),
    (
        HIVE_HKCU,
        r"Software\Policies\Microsoft\Edge",
        "QuicAllowed",
        0,
        TYPE_DWORD,
    ),
    (
        HIVE_HKCU,
        r"Software\Policies\Mozilla\Firefox\DNSOverHTTPS",
        "Enabled",
        0,
        TYPE_DWORD,
    ),
)
_DOH_KEYS: tuple[tuple[str, str, str, Any, str], ...] = (
    (
        HIVE_HKLM,
        r"SYSTEM\CurrentControlSet\Services\Dnscache\Parameters",
        "EnableAutoDoh",
        0,
        TYPE_DWORD,
    ),
    (
        HIVE_HKLM,
        r"SOFTWARE\Policies\Microsoft\Windows NT\DNSClient",
        "DoHPolicy",
        2,
        TYPE_DWORD,
    ),
    (
        HIVE_HKLM,
        r"SOFTWARE\Policies\Google\Chrome",
        "QuicAllowed",
        0,
        TYPE_DWORD,
    ),
    (
        HIVE_HKLM,
        r"SOFTWARE\Policies\Microsoft\Edge",
        "QuicAllowed",
        0,
        TYPE_DWORD,
    ),
)


class RegistryBackend(Protocol):
    def get(self, hive: str, path: str, name: str) -> tuple[bool, Any, str]: ...

    def set(
        self, hive: str, path: str, name: str, value: Any, type_name: str
    ) -> None: ...

    def delete(self, hive: str, path: str, name: str) -> None: ...


class MemoryRegistry:
    """In-memory hive for tests. Does not touch the real registry."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str, str], tuple[Any, str]] = {}
        self.denied: set[tuple[str, str]] = set()

    def get(self, hive: str, path: str, name: str) -> tuple[bool, Any, str]:
        hit = self.data.get((hive, path, name))
        if hit is None:
            return False, None, TYPE_SZ
        return True, hit[0], hit[1]

    def set(
        self, hive: str, path: str, name: str, value: Any, type_name: str
    ) -> None:
        if (hive, path) in self.denied:
            raise PermissionError(f"{hive}\\{path}")
        self.data[(hive, path, name)] = (value, type_name)

    def delete(self, hive: str, path: str, name: str) -> None:
        self.data.pop((hive, path, name), None)


class WinregRegistry:
    def get(self, hive: str, path: str, name: str) -> tuple[bool, Any, str]:
        winreg = _winreg()
        if winreg is None:
            return False, None, TYPE_SZ
        try:
            with winreg.OpenKey(_hive_const(winreg, hive), path) as key:
                value, typ = winreg.QueryValueEx(key, name)
        except OSError:
            return False, None, TYPE_SZ
        return True, value, _type_name(winreg, typ)

    def set(
        self, hive: str, path: str, name: str, value: Any, type_name: str
    ) -> None:
        winreg = _winreg()
        if winreg is None:
            raise OSError("winreg missing")
        access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        key = winreg.CreateKeyEx(_hive_const(winreg, hive), path, 0, access)
        try:
            winreg.SetValueEx(key, name, 0, _type_const(winreg, type_name), value)
        finally:
            winreg.CloseKey(key)

    def delete(self, hive: str, path: str, name: str) -> None:
        winreg = _winreg()
        if winreg is None:
            return
        try:
            with winreg.OpenKey(
                _hive_const(winreg, hive),
                path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            return


_backend: RegistryBackend | None = None


def set_registry_backend(backend: RegistryBackend | None) -> None:
    global _backend
    _backend = backend


def _registry() -> RegistryBackend:
    global _backend
    if _backend is None:
        _backend = WinregRegistry()
    return _backend


def _is_windows() -> bool:
    return sys.platform == "win32"


def _winreg() -> Any:
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _hive_const(winreg: Any, hive: str) -> Any:
    if hive == HIVE_HKLM:
        return winreg.HKEY_LOCAL_MACHINE
    return winreg.HKEY_CURRENT_USER


def _type_const(winreg: Any, type_name: str) -> int:
    if type_name == TYPE_DWORD:
        return winreg.REG_DWORD
    return winreg.REG_SZ


def _type_name(winreg: Any, typ: int) -> str:
    if typ == winreg.REG_DWORD:
        return TYPE_DWORD
    return TYPE_SZ


def is_applied(var_dir: Path) -> bool:
    block = _load_state(var_dir).get(STATE_KEY)
    return isinstance(block, dict) and bool(block.get("applied"))


def consume_browser_toast(var_dir: Path) -> bool:
    """True once after apply wrote browser policies (caller shows the toast)."""
    st = _load_state(var_dir)
    block = st.get(STATE_KEY)
    if not isinstance(block, dict) or not block.get("toast"):
        return False
    block["toast"] = False
    st[STATE_KEY] = block
    _save_state(var_dir, st)
    return True


def apply(*, var_dir: Path, log: LogFn = noop) -> bool:
    """Pin DoH/WebRTC policies. True if browser keys were newly written."""
    if not _is_windows():
        log("leak shield: только Windows")
        return False
    st = _load_state(var_dir)
    prev = st.get(STATE_KEY) if isinstance(st.get(STATE_KEY), dict) else {}
    already = bool(prev.get("applied")) and bool(prev.get("keys"))
    backups: list[dict[str, Any]] = (
        list(prev.get("keys") or []) if already else []
    )
    browser_new = False
    wrote = 0
    skipped = 0
    targets = (*_BROWSER_KEYS, *_DOH_KEYS)
    for hive, path, name, value, type_name in targets:
        if not already:
            try:
                existed, old, old_type = _registry().get(hive, path, name)
            except OSError as exc:
                log(f"leak shield: не прочитал {hive}\\{path}\\{name} ({exc})")
                skipped += 1
                continue
            backups.append(
                {
                    "hive": hive,
                    "path": path,
                    "name": name,
                    "existed": bool(existed),
                    "old": old,
                    "old_type": old_type,
                }
            )
        try:
            _registry().set(hive, path, name, value, type_name)
        except OSError as exc:
            log(f"leak shield: нет прав на {hive}\\{path}\\{name} ({exc})")
            skipped += 1
            continue
        wrote += 1
        if (hive, path, name) in {
            (h, p, n) for h, p, n, _v, _t in _BROWSER_KEYS
        }:
            browser_new = True
    if already:
        browser_new = False
    st[STATE_KEY] = {
        "applied": True,
        "keys": backups,
        "toast": bool(browser_new or (already and prev.get("toast"))),
    }
    _save_state(var_dir, st)
    if wrote:
        log(
            "leak shield: Secure DNS и WebRTC host-IP закрыты"
            + (f", пропущено {skipped}" if skipped else "")
        )
    elif skipped:
        log("leak shield: не удалось записать политики (нужны права)")
    return browser_new


def restore(*, var_dir: Path, log: LogFn = noop) -> None:
    st = _load_state(var_dir)
    block = st.get(STATE_KEY)
    if not isinstance(block, dict) or not block.get("keys"):
        if STATE_KEY in st:
            st.pop(STATE_KEY, None)
            _save_state(var_dir, st)
        return
    restored = 0
    for row in block.get("keys") or []:
        if not isinstance(row, dict):
            continue
        hive = str(row.get("hive") or "")
        path = str(row.get("path") or "")
        name = str(row.get("name") or "")
        if not hive or not path or not name:
            continue
        try:
            if row.get("existed"):
                old_type = str(row.get("old_type") or TYPE_SZ)
                _registry().set(hive, path, name, row.get("old"), old_type)
            else:
                _registry().delete(hive, path, name)
            restored += 1
        except OSError as exc:
            log(f"leak shield: не вернул {hive}\\{path}\\{name} ({exc})")
    st.pop(STATE_KEY, None)
    _save_state(var_dir, st)
    if restored:
        log("leak shield: политики браузера и DoH вернул")
