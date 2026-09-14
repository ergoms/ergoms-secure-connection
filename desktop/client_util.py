"""Small helpers shared by OpsClient mixins (no OpsClient import)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from desktop import procutil

HELPER_CMDLINE = (
    "desktop pac-serve",
    "-m desktop pac-serve",
    "watch --daemon",
    "-m desktop watch",
    "connect_socks.py",
    "connect-socks",
)


def which(name: str) -> str | None:
    return shutil.which(name)


def wait_port(host: str, port: int, *, timeout: float = 10.0) -> bool:
    return procutil.wait_port_open(host, port, timeout=timeout)


def pid_from_file(path: Path, *, unlink: bool = False) -> int | None:
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid = 0
    if unlink:
        path.unlink(missing_ok=True)
    return pid or None


def find_pythonw() -> str | None:
    """Prefer pythonw.exe so child processes never flash a console."""
    if sys.platform != "win32":
        return which("python3") or which("python")
    candidates: list[Path] = []
    exe = Path(sys.executable)
    if exe.name.lower() in ("python.exe", "python3.exe"):
        pw = exe.with_name("pythonw.exe")
        if pw.is_file():
            candidates.append(pw)
    for name in ("pythonw.exe", "python.exe", "python3.exe"):
        found = which(name)
        if found and "WindowsApps" not in found:
            candidates.append(Path(found))
    candidates.append(exe)
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()).lower() if c.is_file() else ""
        if not key or key in seen or "WindowsApps" in key:
            continue
        seen.add(key)
        if c.name.lower() == "pythonw.exe":
            return str(c.resolve())
    for c in candidates:
        if c.is_file() and "WindowsApps" not in str(c):
            return str(c.resolve())
    return None
