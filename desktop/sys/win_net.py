"""Windows IPv4 route / interface helpers (no-op on other platforms)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from desktop import procutil

_IFACE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s*$")


@dataclass(frozen=True)
class NetshIface:
    idx: int
    metric: int
    mtu: int
    state: str
    name: str

    @property
    def up(self) -> bool:
        return self.state.lower() in {"connected", "подключен", "подключено"}


def route_print_v4(*, timeout: float = 8.0) -> str:
    if sys.platform != "win32":
        return ""
    try:
        r = procutil.run(["route", "print", "-4"], timeout=timeout)
    except OSError:
        return ""
    return r.stdout or ""


def netsh_ipv4_interfaces(*, timeout: float = 5.0) -> list[NetshIface]:
    if sys.platform != "win32":
        return []
    try:
        r = procutil.run(
            ["netsh", "interface", "ipv4", "show", "interfaces"],
            timeout=timeout,
        )
    except OSError:
        return []
    out: list[NetshIface] = []
    for line in (r.stdout or "").splitlines():
        m = _IFACE_RE.match(line)
        if not m:
            continue
        out.append(
            NetshIface(
                idx=int(m.group(1)),
                metric=int(m.group(2)),
                mtu=int(m.group(3)),
                state=m.group(4),
                name=m.group(5).strip(),
            )
        )
    return out


def best_interface_index(dest_ip: str) -> int | None:
    """Windows GetBestInterface for an IPv4 address."""
    if sys.platform != "win32" or not dest_ip:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        dest_n = ctypes.windll.ws2_32.inet_addr(dest_ip.encode("ascii"))  # type: ignore[attr-defined]
        if dest_n == 0xFFFFFFFF:
            return None
        idx = wintypes.DWORD()
        err = ctypes.windll.iphlpapi.GetBestInterface(dest_n, ctypes.byref(idx))  # type: ignore[attr-defined]
        if err or not idx.value:
            return None
        return int(idx.value)
    except Exception:  # noqa: BLE001
        return None


def run_route_lines(lines: list[str], *, timeout: float = 8.0) -> bool:
    """Run space-separated route/netsh/ip command lines. Returns True if all ran."""
    ok = True
    for raw in lines:
        parts = [p for p in str(raw).split() if p]
        if not parts:
            continue
        try:
            r = procutil.run(parts, timeout=timeout)
        except OSError:
            ok = False
            continue
        if r.returncode != 0:
            ok = False
    return ok
