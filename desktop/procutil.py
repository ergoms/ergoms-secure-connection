"""Subprocess helpers that do not flash console windows on Windows."""

from __future__ import annotations

import base64
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


def process_basename(pid: int) -> str:
    """Lowercase executable filename for pid, or empty if unknown."""
    if pid <= 0:
        return ""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    path = buf.value.replace("/", "\\")
                    return path.rsplit("\\", 1)[-1].lower()
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return ""
        return ""
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip().lower()
    except OSError:
        return ""


def is_sing_box_pid(pid: int) -> bool:
    return process_basename(pid) in {"sing-box", "sing-box.exe"}


def pids_named(*names: str) -> list[int]:
    """PIDs whose executable basename matches (no PowerShell)."""
    want = {n.lower() for n in names if n}
    if not want:
        return []
    if sys.platform == "win32":
        return _pids_named_win(want)
    found: list[int] = []
    proc = "/proc"
    if not os.path.isdir(proc):
        return found
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "comm"), encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip().lower()
        except OSError:
            continue
        if comm in want:
            found.append(int(name))
    return found


def _pids_named_win(want: set[str]) -> list[int]:
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1, 0xFFFFFFFF):
        return []
    found: list[int] = []
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return []
        while True:
            if pe.szExeFile.lower() in want:
                found.append(int(pe.th32ProcessID))
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return found


def pid_alive(pid: int, *, names: Sequence[str] | None = None) -> bool:
    if pid <= 0:
        return False
    alive = False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                alive = True
        except Exception:  # noqa: BLE001
            alive = False
    else:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    if not alive:
        return False
    if not names:
        return True
    return process_basename(pid) in {n.lower() for n in names}


def _wait_pid_dead(pid: int, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.08)
    return not pid_alive(pid)


def kill_pid(pid: int) -> bool:
    """Best-effort kill. Returns True if the process is gone."""
    if pid <= 0:
        return True
    invalidate_proc_cache()
    if sys.platform == "win32":
        run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return _wait_pid_dead(pid)
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    if _wait_pid_dead(pid, timeout=1.2):
        return True
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    return _wait_pid_dead(pid, timeout=1.0)


def elevate_kill_pids(pids: Sequence[int]) -> bool:
    """Ask for admin/root once and kill leftover PIDs (TUN sing-box)."""
    targets = [int(p) for p in pids if p and p > 0 and pid_alive(p)]
    if not targets:
        return True
    if is_admin():
        return all(kill_pid(p) for p in targets)
    invalidate_proc_cache()
    if sys.platform == "win32":
        return _elevate_taskkill_win(targets)
    return _elevate_kill_linux(targets)


def _elevate_taskkill_win(pids: Sequence[int]) -> bool:
    import ctypes

    params = "/T /F " + " ".join(f"/PID {int(p)}" for p in pids)
    rc = int(
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", "taskkill.exe", params, None, 0
        )
    )
    if rc <= 32:
        return False
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        if not any(pid_alive(p) for p in pids):
            return True
        time.sleep(0.2)
    return not any(pid_alive(p) for p in pids)


def _elevate_kill_linux(pids: Sequence[int]) -> bool:
    ids = [str(int(p)) for p in pids]
    for wrapper in (
        ["pkexec", "kill", "-9", *ids],
        ["sudo", "-n", "kill", "-9", *ids],
        ["sudo", "kill", "-9", *ids],
    ):
        try:
            r = run(wrapper, timeout=60)
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
        if r.returncode == 0 or not any(pid_alive(int(p)) for p in ids):
            return not any(pid_alive(int(p)) for p in ids)
    return not any(pid_alive(int(p)) for p in ids)


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


def pids_cmdline_match(substr: str, *, cache: bool = True) -> list[int]:
    """PIDs whose command line contains substr (case-insensitive on Windows).

    The search string is not placed literally on the scanner command line,
    otherwise the PowerShell/pgrep helper matches itself (false 'VPN on').
    """
    if not substr:
        return []
    key = ("cmdline", substr.lower())
    if cache:
        cached = _proc_cache_get(key)
        if cached is not None:
            return cached
    found: list[int] = []
    me = os.getpid()
    if sys.platform == "win32":
        token = base64.b64encode(substr.encode("utf-8")).decode("ascii")
        ps = (
            f"$n = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{token}')); "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.CommandLine -and $_.CommandLine.ToLower().Contains($n.ToLower()) "
            "} | Select-Object -ExpandProperty ProcessId"
        )
        r = run(["powershell", "-NoProfile", "-Command", ps])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != me:
                    found.append(pid)
    else:
        # [s]ing-box… trick so pgrep -f does not match its own argv
        if len(substr) >= 2:
            pattern = f"[{substr[0]}]{substr[1:]}"
        else:
            pattern = substr
        r = run(["pgrep", "-f", pattern])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != me:
                    found.append(pid)
    if cache:
        return _proc_cache_set(key, found)
    return found


def kill_pids(
    pids: Sequence[int], *, exclude: int = 0, elevate_if_needed: bool = False
) -> list[int]:
    """Kill unique PIDs; return those that actually died."""
    invalidate_proc_cache()
    targeted: list[int] = []
    seen: set[int] = set()
    for pid in pids:
        if pid <= 0 or pid == exclude or pid in seen:
            continue
        seen.add(pid)
        if pid_alive(pid):
            targeted.append(pid)
            kill_pid(pid)
    leftover = [p for p in targeted if pid_alive(p)]
    if leftover and elevate_if_needed:
        elevate_kill_pids(leftover)
    return [p for p in targeted if not pid_alive(p)]


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
