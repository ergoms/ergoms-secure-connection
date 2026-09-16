"""Windows IPv4 route / interface helpers (no-op on other platforms)."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

from desktop import procutil

_IFACE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s*$")
_IFACE_UP = frozenset(
    {
        "connected",
        "подключен",
        "подключено",
        "подключена",
    }
)
_DEFAULT_METRIC_RE = re.compile(r"по\s+умолчанию", re.IGNORECASE)


@dataclass(frozen=True)
class NetshIface:
    idx: int
    metric: int
    mtu: int
    state: str
    name: str

    @property
    def up(self) -> bool:
        return self.state.lower() in _IFACE_UP


def _win_cp_name(kind: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        fn = (
            ctypes.windll.kernel32.GetOEMCP
            if kind == "oem"
            else ctypes.windll.kernel32.GetACP
        )
        cp = int(fn())
        if cp in (0, 65001):
            return "utf-8"
        return f"cp{cp}"
    except Exception:  # noqa: BLE001
        return "cp866" if kind == "oem" else "cp1251"


def decode_win_console(raw: bytes) -> str:
    """Decode route/netsh output. OEM CP866 as UTF-8 looks like CJK (㬮)."""
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    encodings: list[str] = []
    for enc in (
        _win_cp_name("oem"),
        _win_cp_name("ansi"),
        "utf-8",
        "cp866",
        "cp1251",
    ):
        if enc and enc not in encodings:
            encodings.append(enc)
    best = ""
    best_score: int | None = None
    for enc in encodings:
        try:
            text = raw.decode(enc)
            score = 2
        except LookupError:
            continue
        except UnicodeDecodeError:
            text = raw.decode(enc, errors="replace")
            score = -text.count("\ufffd")
        low = text.lower()
        if "умолчан" in low or "подключен" in low:
            score += 5
        if best_score is None or score > best_score:
            best_score = score
            best = text
    return best or raw.decode("utf-8", errors="replace")


def normalize_win_net_text(text: str) -> str:
    """route print uses 'по умолчанию' where English has 'Default'."""
    return _DEFAULT_METRIC_RE.sub("Default", text or "")


def _run_console(args: list[str], *, timeout: float) -> str:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=procutil.creationflags(),
            startupinfo=procutil.startupinfo(),
        )
    except OSError:
        return ""
    return normalize_win_net_text(decode_win_console(r.stdout or b""))


def route_print_v4(*, timeout: float = 8.0) -> str:
    if sys.platform != "win32":
        return ""
    return _run_console(["route", "print", "-4"], timeout=timeout)


def netsh_ipv4_interfaces(*, timeout: float = 5.0) -> list[NetshIface]:
    if sys.platform != "win32":
        return []
    text = _run_console(
        ["netsh", "interface", "ipv4", "show", "interfaces"],
        timeout=timeout,
    )
    out: list[NetshIface] = []
    for line in text.splitlines():
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
