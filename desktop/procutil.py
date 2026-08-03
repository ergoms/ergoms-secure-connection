"""Subprocess helpers that do not flash console windows on Windows."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence


def creationflags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


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
    if sys.platform == "win32":
        run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def pids_listening_on(port: int, host: str = "127.0.0.1") -> list[int]:
    """PIDs with a TCP LISTEN socket on host:port (best-effort)."""
    if port <= 0:
        return []
    found: list[int] = []
    if sys.platform == "win32":
        # OwnProcess may repeat for IPv4/IPv6; keep order stable / unique.
        ps = (
            f"$h='{host}'; $p={int(port)}; "
            "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
            "Where-Object { $_.LocalPort -eq $p -and ($_.LocalAddress -eq $h -or $_.LocalAddress -eq '0.0.0.0' -or $_.LocalAddress -eq '::') } | "
            "Select-Object -ExpandProperty OwningProcess -Unique"
        )
        r = run(["powershell", "-NoProfile", "-Command", ps])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                found.append(int(line))
        return found
    # ss: users:(("ssh",pid=123,fd=3))
    r = run(["ss", "-ltnp", f"sport = :{int(port)}"])
    import re

    for m in re.finditer(r"pid=(\d+)", r.stdout or ""):
        pid = int(m.group(1))
        if pid not in found:
            found.append(pid)
    return found


def pids_cmdline_match(substr: str) -> list[int]:
    """PIDs whose command line contains substr (case-insensitive on Windows)."""
    if not substr:
        return []
    found: list[int] = []
    if sys.platform == "win32":
        # Escape single quotes for PowerShell string literal.
        needle = substr.replace("'", "''")
        ps = (
            "Get-CimInstance Win32_Process | "
            f"Where-Object {{ $_.CommandLine -and $_.CommandLine -like '*{needle}*' }} | "
            "Select-Object -ExpandProperty ProcessId"
        )
        r = run(["powershell", "-NoProfile", "-Command", ps])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                found.append(int(line))
        return found
    r = run(["pgrep", "-f", substr])
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            found.append(int(line))
    return found


def kill_pids(pids: Sequence[int], *, exclude: int = 0) -> list[int]:
    """Kill unique PIDs; return those that were targeted."""
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
