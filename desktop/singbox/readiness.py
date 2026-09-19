"""TUN / SOCKS readiness from sing-box log tails. Pure functions, no process state."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

from desktop import procutil
from desktop.tun import wait_tun_iface
from lib.netutil import port_open

LogFn = Callable[[str], None]

# PAC/browser traffic drowns Wintun FATAL if we only keep the last 80 lines.
TUN_LOG_SCAN_LINES = 4000


def tun_create_conflict(lines: list[str]) -> bool:
    """Wintun leftover: CreateAdapter fails after ~15s if we wait it out."""
    text = "\n".join(lines).lower()
    return "already exists" in text or "cannot create a file" in text


def tun_adapter_busy(lines: list[str]) -> bool:
    text = "\n".join(lines).lower()
    return (
        tun_create_conflict(lines)
        or "configure tun interface" in text
        or "wintun" in text
        or "take too much time" in text
    )


def tun_log_started(lines: list[str]) -> bool:
    for raw in lines:
        low = raw.lower()
        if "inbound/tun" in low and "started at" in low:
            return True
    return False


def tun_still_opening(lines: list[str]) -> bool:
    if tun_log_started(lines):
        return False
    tail = "\n".join(lines).lower()
    return "take too much time" in tail or "configure tun interface" in tail


def tun_inbound_ready(
    lines: list[str],
    *,
    platform: str | None = None,
    iface_up: Callable[[], Any] | None = None,
) -> bool:
    plat = sys.platform if platform is None else platform
    if plat != "win32":
        return True
    if tun_still_opening(lines):
        return False
    if tun_log_started(lines):
        return True
    probe = iface_up or (lambda: wait_tun_iface(timeout=0.05))
    return bool(probe())


# Healthy CreateAdapter is <1s once the name is free. Leftover name reservation
# logs "take too much time" at ~10s and FATAL already exists at ~15s.
_LEFTOVER_OPEN_SECS = 2.5


def wait_ready(
    *,
    socks_port: int,
    http_port: int,
    enable_tun: bool,
    pid: int | None,
    log: LogFn,
    tail: Callable[[int], list[str]],
    live_pid: Callable[[], int | None],
    timeout: float = 22.0,
    platform: str | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    interval = 0.05
    socks_ok = False
    socks_at: float | None = None
    extended = False
    while time.monotonic() < deadline:
        lines = tail(TUN_LOG_SCAN_LINES) if enable_tun else tail(80)
        if enable_tun and tun_create_conflict(lines) and not tun_log_started(lines):
            log("TUN: leftover Wintun — пересоздаю адаптер")
            return False
        if port_open("127.0.0.1", socks_port, timeout=0.08):
            socks_ok = True
            if socks_at is None:
                socks_at = time.monotonic()
            if not enable_tun or tun_inbound_ready(lines, platform=platform):
                kind = "mixed + TUN" if enable_tun else "mixed"
                log(f"sing-box слушает SOCKS :{socks_port} и HTTP :{http_port} ({kind})")
                return True
            hung = (time.monotonic() - socks_at) >= _LEFTOVER_OPEN_SECS
            opening = tun_still_opening(lines) and not tun_create_conflict(lines)
            if enable_tun and (opening or hung):
                iface_up = wait_tun_iface(timeout=0.05)
                if iface_up and opening and not extended:
                    deadline = max(deadline, time.monotonic() + 15.0)
                    extended = True
                    log("Wintun ещё создаёт адаптер — жду, процесс не убиваю")
                elif hung and not iface_up:
                    log("TUN: leftover Wintun — пересоздаю адаптер")
                    return False
        check = pid or live_pid()
        if check and not procutil.pid_alive(check) and not live_pid():
            if enable_tun:
                log("TUN: sing-box умер — пересоздаю адаптер")
            return False
        time.sleep(interval)
        interval = min(interval * 1.3, 0.2)
    if socks_ok and enable_tun:
        log(f"sing-box слушает SOCKS :{socks_port}, но TUN ещё не поднялся")
    return False
