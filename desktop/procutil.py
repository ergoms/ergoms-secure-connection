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
