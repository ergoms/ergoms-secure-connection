"""Subprocess helpers that do not flash console windows on Windows."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Callable, Sequence

_PROC_CACHE_TTL = 1.0
_proc_cache: dict[tuple, tuple[float, list[int]]] = {}


def creationflags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


def is_admin() -> bool:
    """True when this process already has admin/root (no UAC/sudo needed)."""
    if sys.platform == "win32":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False
    return bool(hasattr(os, "geteuid") and os.geteuid() == 0)


def invalidate_proc_cache() -> None:
    """Drop cached PID lookups (call before kill/stop)."""
    _proc_cache.clear()


def _proc_cache_get(key: tuple) -> list[int] | None:
    entry = _proc_cache.get(key)
    if entry and time.monotonic() - entry[0] < _PROC_CACHE_TTL:
        return list(entry[1])
    return None


def _proc_cache_set(key: tuple, value: list[int]) -> list[int]:
    _proc_cache[key] = (time.monotonic(), list(value))
    return value


def run(
    args: Sequence[str],
    *,
    check: bool = False,
    text: bool = True,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        check=check,
        env=env,
        cwd=cwd,
        timeout=timeout,
        creationflags=creationflags(),
    )


def popen(
    args: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    detached: bool = False,
) -> subprocess.Popen:
    kw: dict = {
        "args": list(args),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "env": env,
        "cwd": cwd,
        "creationflags": creationflags(),
    }
    # Survive parent exit (CLI `on` returns immediately; bridge must keep running).
    if detached:
        if sys.platform == "win32":
            kw["creationflags"] = creationflags() | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kw["start_new_session"] = True
    return subprocess.Popen(**kw)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    invalidate_proc_cache()
    if sys.platform == "win32":
        run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _pids_listening_on_netstat(port: int, host: str) -> list[int]:
    """Fast Windows path via netstat (~10–50 ms vs seconds for Get-NetTCPConnection)."""
    r = run(["netstat", "-ano", "-p", "tcp"])
    found: list[int] = []
    seen: set[int] = set()
    port_s = f":{int(port)}"
    host_l = host.lower()
    for line in (r.stdout or "").splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        if not local_addr.endswith(port_s):
            continue
        addr = local_addr.rsplit(":", 1)[0]
        if host_l not in ("127.0.0.1", "localhost", "::1"):
            if addr not in (host_l, "0.0.0.0", "::", "[::]"):
                continue
        elif addr not in ("127.0.0.1", "0.0.0.0", "::", "[::]", "localhost"):
            continue
        pid_s = parts[-1]
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid not in seen:
            seen.add(pid)
            found.append(pid)
    return found


def pids_listening_on(
    port: int, host: str = "127.0.0.1", *, cache: bool = True
) -> list[int]:
    """PIDs with a TCP LISTEN socket on host:port (best-effort)."""
    if port <= 0:
        return []
    key = ("listen", int(port), host)
    if cache:
        cached = _proc_cache_get(key)
        if cached is not None:
            return cached
    found: list[int] = []
    if sys.platform == "win32":
        found = _pids_listening_on_netstat(port, host)
    else:
        r = run(["ss", "-ltnp", f"sport = :{int(port)}"])
        for m in re.finditer(r"pid=(\d+)", r.stdout or ""):
            pid = int(m.group(1))
            if pid not in found:
                found.append(pid)
    if cache:
        return _proc_cache_set(key, found)
    return found


def _wmi_escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "[%]").replace("_", "[_]")


def pids_cmdline_match(substr: str, *, cache: bool = True) -> list[int]:
    """PIDs whose command line contains substr (case-insensitive on Windows)."""
    if not substr:
        return []
    key = ("cmdline", substr.lower())
    if cache:
        cached = _proc_cache_get(key)
        if cached is not None:
            return cached
    found: list[int] = []
    if sys.platform == "win32":
        needle = _wmi_escape_like(substr).replace("'", "''")
        ps = (
            "Get-CimInstance Win32_Process "
            f"-Filter \"CommandLine LIKE '%{needle}%'\" | "
            "Select-Object -ExpandProperty ProcessId"
        )
        r = run(["powershell", "-NoProfile", "-Command", ps])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                found.append(int(line))
    else:
        r = run(["pgrep", "-f", substr])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                found.append(int(line))
    if cache:
        return _proc_cache_set(key, found)
    return found


def kill_pids(pids: Sequence[int], *, exclude: int = 0) -> list[int]:
    """Kill unique PIDs; return those that were targeted."""
    invalidate_proc_cache()
    killed: list[int] = []
    seen: set[int] = set()
    for pid in pids:
        if pid <= 0 or pid == exclude or pid in seen:
            continue
        seen.add(pid)
        if pid_alive(pid):
            kill_pid(pid)
            killed.append(pid)
    return killed


def wait_port_open(
    host: str,
    port: int,
    *,
    timeout: float = 10.0,
    interval_start: float = 0.05,
    interval_max: float = 0.25,
    probe: Callable[[], bool] | None = None,
) -> bool:
    """Poll until TCP port accepts connections or timeout expires."""
    import socket

    def default_probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            return False

    check = probe or default_probe
    deadline = time.monotonic() + timeout
    interval = interval_start
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
        interval = min(interval * 1.5, interval_max)
    return False
