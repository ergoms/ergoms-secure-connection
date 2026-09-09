"""sing-box binary: find, download, stop leftover processes."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from desktop import procutil
from desktop.paths import bundle_dir, is_frozen

LogFn = Callable[[str], None]

SING_BOX_VERSION = "1.11.15"


def _noop(msg: str) -> None:
    pass


def _resolve_host(host: str) -> str | None:
    host = (host or "").strip()
    if not host:
        return None
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def detect_bind_interface(dest: str) -> str | None:
    """Interface used to reach dest on the underlay (not the TUN device).

    With auto_route TUN, sing-box auto_detect_interface often picks the TUN
    iface; dialing Squid then fails with "no route to internet". Bind Squid /
    direct outbounds to the physical NIC instead.
    """
    dest = (dest or "").strip()
    if not dest:
        return None
    if sys.platform.startswith("linux"):
        try:
            r = subprocess.run(
                ["ip", "-4", "route", "get", dest],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        parts = (r.stdout or "").split()
        if "dev" not in parts:
            return None
        i = parts.index("dev")
        if i + 1 >= len(parts):
            return None
        dev = parts[i + 1].strip()
        if not dev or dev.startswith("ops-content") or dev.startswith("ergoms-vpn"):
            return None
        return dev
    return None


def _arch_tag() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "amd64"


def _direct_python_paths() -> list[str]:
    """Interpreters used by reverse-ssh / PAC (avoid TUN loops).

    Other Python installs (pip, poetry, venv) stay on the default proxy path.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(p: Path | None) -> None:
        if p is None or not p.is_file():
            return
        resolved = p.resolve()
        key = str(resolved).lower()
        if key in seen or "WindowsApps" in str(resolved):
            return
        seen.add(key)
        found.append(str(resolved))

    exe = Path(sys.executable)
    add(exe)
    if sys.platform == "win32":
        siblings = [exe.with_name(n) for n in ("pythonw.exe", "python.exe", "python3.exe")]
        for sib in siblings:
            add(sib)
        # Frozen ErgomsVPN.exe: ProxyCommand falls back to PATH pythonw
        if not any(s.is_file() for s in siblings):
            for name in ("pythonw.exe", "python.exe", "python3.exe"):
                w = shutil.which(name)
                if w and "WindowsApps" not in w:
                    add(Path(w))
                    break
    else:
        for name in ("python3", "python"):
            w = shutil.which(name)
            if w:
                add(Path(w))
    return found


RUSTDESK_PORTS = [21114, 21115, 21116, 21117, 21118, 21119]


class TunManager:
    """Locate/download sing-box and stop leftover TUN processes."""

    def __init__(self, var_dir: Path, tools_dir: Path, logs_dir: Path, log: LogFn = _noop) -> None:
        self.var_dir = var_dir
        self.tools_dir = tools_dir
        self.log = log
        self.config_path = var_dir / "sing-box-tun.json"
        self.pid_path = var_dir / "sing-box.pid"
        self.log_path = logs_dir / "sing-box.log"
        self._pid_scan_at = 0.0
        self._pid_scan_result: int | None = None

    def _bin_name(self) -> str:
        return "sing-box.exe" if sys.platform == "win32" else "sing-box"

    @staticmethod
    def _is_native_sing_box(path: Path) -> bool:
        """Reject wrong-OS leftovers (e.g. Windows PE on Linux tools/)."""
        if not path.is_file():
            return False
        try:
            with open(path, "rb") as fh:
                magic = fh.read(4)
        except OSError:
            return False
        if sys.platform == "win32":
            return magic[:2] == b"MZ"
        # Linux/macOS: ELF; never treat PE (.exe) as usable
        if magic[:2] == b"MZ" or path.suffix.lower() == ".exe":
            return False
        return magic == b"\x7fELF" or sys.platform == "darwin"

    def find_sing_box(self, explicit: str = "") -> Path | None:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        packed = self._materialize_bundled()
        if packed:
            candidates.append(packed)
        env = (os.environ.get("SING_BOX_PATH") or "").strip()
        if env:
            candidates.append(Path(env))
        name = self._bin_name()
        candidates.append(self.tools_dir / name)
        candidates.append(self.tools_dir / "sing-box" / name)
        if sys.platform == "win32":
            candidates.append(self.tools_dir / "sing-box.exe")
        else:
            candidates.append(self.tools_dir / "sing-box")
        which = shutil.which(name)
        if which:
            candidates.append(Path(which))
        seen: set[str] = set()
        for c in candidates:
            key = str(c.resolve()) if c.exists() else str(c)
            if key in seen:
                continue
            seen.add(key)
            if not self._is_native_sing_box(c):
                continue
            if sys.platform != "win32" and not os.access(c, os.X_OK):
                try:
                    c.chmod(c.stat().st_mode | 0o111)
                except OSError:
                    continue
            if sys.platform == "win32" or os.access(c, os.X_OK):
                return c.resolve()
        return None

    def _bundled_sing_box(self) -> Path | None:
        packed = bundle_dir() / "tools" / self._bin_name()
        if self._is_native_sing_box(packed):
            return packed
        return None

    def _materialize_bundled(self) -> Path | None:
        """Copy packed sing-box to writable tools/ (UAC + onefile temp)."""
        src = self._bundled_sing_box()
        if src is None:
            return None
        if not is_frozen():
            return src
        dest = self.tools_dir / src.name
        try:
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            if dest.is_file() and dest.stat().st_size == src.stat().st_size:
                return dest
            shutil.copy2(src, dest)
            return dest
        except OSError:
            return src

    def running(self) -> bool:
        return self.pid() is not None

    def pid(self) -> int | None:
        if self.pid_path.is_file():
            try:
                pid = int(self.pid_path.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = None
            if pid and procutil.pid_alive(pid) and procutil.is_sing_box_pid(pid):
                return pid
        now = time.monotonic()
        if now - self._pid_scan_at < 2.0 and self._pid_scan_result:
            if procutil.pid_alive(self._pid_scan_result) and procutil.is_sing_box_pid(
                self._pid_scan_result
            ):
                return self._pid_scan_result
        found = self._find_sing_box_pid()
        if found and not procutil.is_sing_box_pid(found):
            found = None
        self._pid_scan_at = now
        self._pid_scan_result = found
        if found:
            self.pid_path.write_text(str(found), encoding="utf-8")
            return found
        return None

    def stop(self) -> None:
        pid = self.pid()
        targets: list[int] = []
        if pid:
            targets.append(pid)
        for orphan in procutil.pids_named("sing-box.exe", "sing-box"):
            if orphan not in targets:
                targets.append(orphan)
        died = procutil.kill_pids(targets)
        leftover = [p for p in targets if procutil.pid_alive(p)]
        self._pid_scan_at = 0.0
        self._pid_scan_result = None
        self.pid_path.unlink(missing_ok=True)
        if leftover:
            self.log(
                f"legacy TUN: sing-box pid={','.join(str(p) for p in leftover)} "
                "ещё жив (нужен UAC)"
            )
            return
        if died:
            self.log(f"legacy TUN: pid={','.join(str(p) for p in died)} остановлен")
            self.log("TUN выключен")

    def _find_sing_box_pid(self) -> int | None:
        for pid in procutil.pids_named("sing-box.exe", "sing-box"):
            return pid
        return None

    def ensure_downloaded(self, proxy_url: str | None = None) -> Path:
        """Download sing-box for current OS/arch into tools/."""
        existing = self.find_sing_box()
        if existing:
            return existing
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        ver = SING_BOX_VERSION
        arch = _arch_tag()
        if sys.platform == "win32":
            asset = f"sing-box-{ver}-windows-{arch}.zip"
            url = f"https://github.com/SagerNet/sing-box/releases/download/v{ver}/{asset}"
            archive = self.tools_dir / asset
            target = self.tools_dir / "sing-box.exe"
            self.log(f"Downloading {asset}…")
            self._download_file(url, archive, proxy_url=proxy_url)
            with zipfile.ZipFile(archive, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("sing-box.exe"):
                        with zf.open(name) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        break
                else:
                    raise RuntimeError("sing-box.exe not found in zip")
            archive.unlink(missing_ok=True)
        else:
            asset = f"sing-box-{ver}-linux-{arch}.tar.gz"
            url = f"https://github.com/SagerNet/sing-box/releases/download/v{ver}/{asset}"
            archive = self.tools_dir / asset
            target = self.tools_dir / "sing-box"
            self.log(f"Downloading {asset}…")
            self._download_file(url, archive, proxy_url=proxy_url)
            with tarfile.open(archive, "r:gz") as tf:
                member = next(
                    (
                        m
                        for m in tf.getmembers()
                        if m.name.endswith("/sing-box") or m.name == "sing-box"
                    ),
                    None,
                )
                if not member:
                    raise RuntimeError("sing-box not found in tar.gz")
                f = tf.extractfile(member)
                if not f:
                    raise RuntimeError("cannot extract sing-box")
                with open(target, "wb") as dst:
                    shutil.copyfileobj(f, dst)
            target.chmod(0o755)
            archive.unlink(missing_ok=True)
        self.log(f"sing-box → {target}")
        return target

    def _download_file(self, url: str, dest: Path, *, proxy_url: str | None = None) -> None:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if curl:
            args = [curl, "-fsSL", "--connect-timeout", "30", "--max-time", "180", "-o", str(dest), url]
            if sys.platform == "win32":
                args.insert(1, "--ssl-no-revoke")
            if proxy_url:
                args[1:1] = ["--proxy", proxy_url]
            else:
                try:
                    with socket.create_connection(("127.0.0.1", 1080), timeout=0.3):
                        args[1:1] = ["--proxy", "socks5h://127.0.0.1:1080"]
                except OSError:
                    pass
            r = procutil.run(args, timeout=200)
            if r.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000:
                return
            self.log(f"curl download failed (exit={r.returncode})")
        handlers = []
        if proxy_url:
            handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(url, timeout=120) as resp, open(dest, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
