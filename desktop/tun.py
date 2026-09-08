"""TUN-over-SOCKS via sing-box (Windows / Linux)."""

from __future__ import annotations

import json
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
from typing import Any, Callable

from desktop import procutil
from desktop.paths import bundle_dir, is_frozen
from lib.http_via_socks import bypass_to_singbox

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
        if not dev or dev.startswith("ops-content"):
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
    """Only interpreters used by ProxyCommand / HTTP bridge (avoid TUN loops).

    Other Python installs (pip, poetry, venv) stay on the default socks-out path.
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
        # Frozen OpsContent.exe: ProxyCommand falls back to PATH pythonw
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


def _remote_desktop_process_names() -> list[str]:
    """Kept for optional TUN bypass; ID/relay on the VPN VPS must use VLESS (office RST on :21116)."""
    return ["rustdesk.exe", "RustDesk.exe"]


RUSTDESK_PORTS = [21114, 21115, 21116, 21117, 21118, 21119]


def _remote_desktop_process_paths() -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        if not p.is_file():
            return
        resolved = p.resolve()
        key = str(resolved).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(str(resolved).replace("\\", "/"))

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or ""
        roots = [
            Path(r"C:\Program Files\RustDesk"),
            Path(r"C:\Program Files (x86)\RustDesk"),
        ]
        if local:
            roots.extend([Path(local) / "rustdesk", Path(local) / "RustDesk"])
        for root in roots:
            add(root / "rustdesk.exe")
            add(root / "RustDesk.exe")
    else:
        for name in ("rustdesk",):
            w = shutil.which(name)
            if w:
                add(Path(w))
    return found


class TunManager:
    """Start/stop sing-box TUN that forwards into an existing local SOCKS5."""

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

    def build_config(
        self,
        *,
        socks_host: str,
        socks_port: int,
        exclude_ips: list[str],
        bypass_hosts: list[str] | None = None,
        mtu: int = 1500,
    ) -> dict[str, Any]:
        route_exclude = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "224.0.0.0/4",
        ]
        # ssh / sing-box / GUI — by name. Python — only ops-content's interpreter
        # (process_path), so pip/poetry in other installs go through TUN.
        proc_names = [
            "sing-box",
            "sing-box.exe",
            "ssh",
            "ssh.exe",
            "OpsContent.exe",
        ]
        # Docker Desktop / WSL / Hyper-V: do not half-capture VM traffic.
        # Containers should use HTTP_PROXY → host bridge (var/docker.env).
        docker_wsl_procs = [
            "vmmem",
            "vmmemWSL",
            "wsl.exe",
            "wslhost.exe",
            "wslrelay.exe",
            "wslservice.exe",
            "WSLService.exe",
            "vmcompute.exe",
            "vmwp.exe",
            "com.docker.backend.exe",
            "com.docker.build.exe",
            "com.docker.proxy.exe",
            "com.docker.admin.exe",
            "com.docker.dev-envs.exe",
            "Docker Desktop.exe",
            "docker.exe",
            "dockerd.exe",
            "vpnkit.exe",
            "vpnkit-bridge.exe",
        ]
        # Order matters:
        # 1) hijack DNS before ip_is_private (TUN DNS 172.19.0.2:53 is private).
        # 2) Docker/WSL after hijack so UDP/53 is answered by sing-box DNS module
        #    (dns-local), then remaining VM traffic stays direct.
        # SSH DynamicForward is TCP-only — plain UDP DNS via socks-out fails (EOF).
        rules: list[dict[str, Any]] = []
        ips = [ip for ip in exclude_ips if ip]
        if ips:
            rules.append({"ip_cidr": [f"{ip}/32" for ip in ips], "port": 443, "outbound": "direct"})
            rules.append({"ip_cidr": [f"{ip}/32" for ip in ips], "port": RUSTDESK_PORTS, "outbound": "socks-out"})
            rules.append({"ip_cidr": [f"{ip}/32" for ip in ips], "outbound": "direct"})
        bypass_suffixes, bypass_domains = bypass_to_singbox(bypass_hosts or [])
        if bypass_suffixes:
            rules.append({"domain_suffix": bypass_suffixes, "outbound": "direct"})
        if bypass_domains:
            rules.append({"domain": bypass_domains, "outbound": "direct"})
        rules.append({"port": 53, "action": "hijack-dns"})
        rules.append({"process_name": docker_wsl_procs, "outbound": "direct"})
        rules.append({"process_name": proc_names, "outbound": "direct"})
        py_paths = _direct_python_paths()
        if py_paths:
            rules.append({"process_path": py_paths, "outbound": "direct"})
        rules.append({"ip_is_private": True, "outbound": "direct"})

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        mtu_val = max(1280, min(1500, int(mtu)))
        return {
            "log": {
                "level": "info",
                "timestamp": True,
                "output": str(self.log_path).replace("\\", "/"),
            },
            "dns": {
                "servers": [
                    # DoH over SOCKS — OpenSSH DynamicForward has no UDP ASSOCIATE,
                    # so plain UDP 8.8.8.8 via socks-out fails with EOF.
                    {
                        "tag": "dns-proxy",
                        "address": "https://1.1.1.1/dns-query",
                        "detour": "socks-out",
                    },
                    {"tag": "dns-local", "address": "local", "detour": "direct"},
                ],
                "rules": [
                    {
                        # Docker ExtServers (8.8.8.8/1.1.1.1) are often unreachable
                        # from the VM; answer with the host resolver instead.
                        "process_name": docker_wsl_procs,
                        "server": "dns-local",
                    },
                    *(
                        [{"domain_suffix": bypass_suffixes, "server": "dns-local"}]
                        if bypass_suffixes
                        else []
                    ),
                    *(
                        [{"domain": bypass_domains, "server": "dns-local"}]
                        if bypass_domains
                        else []
                    ),
                    {
                        "domain_suffix": [".local", ".lan", ".internal", ".localhost"],
                        "server": "dns-local",
                    },
                ],
                "final": "dns-proxy",
                "strategy": "prefer_ipv4",
            },
            "inbounds": [
                {
                    "type": "tun",
                    "tag": "tun-in",
                    "interface_name": "ops-content-tun",
                    "address": ["172.19.0.1/30"],
                    "mtu": mtu_val,
                    "auto_route": True,
                    "strict_route": False,
                    "stack": "system",
                    "sniff": True,
                    "route_exclude_address": route_exclude,
                }
            ],
            "outbounds": [
                {
                    "type": "socks",
                    "tag": "socks-out",
                    "server": socks_host,
                    "server_port": int(socks_port),
                    "version": "5",
                },
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"},
            ],
            "route": {
                "auto_detect_interface": True,
                "final": "socks-out",
                "rules": rules,
            },
        }

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

    def start(
        self,
        *,
        socks_port: int,
        corporate_proxy: str,
        ssh_host: str,
        sing_box_path: str = "",
        elevate: bool = True,
        bypass_hosts: list[str] | None = None,
        mtu: int = 1500,
        force_restart: bool = False,
    ) -> None:
        exe = self.find_sing_box(sing_box_path)
        if not exe:
            raise RuntimeError(
                "sing-box не найден. Положите бинарник в tools/ "
                "или выполните: ops-content download-sing-box"
            )

        try:
            with socket.create_connection(("127.0.0.1", int(socks_port)), timeout=1.0):
                pass
        except OSError as exc:
            raise RuntimeError(
                f"SOCKS 127.0.0.1:{socks_port} недоступен — сначала включите туннель"
            ) from exc

        exclude: list[str] = []
        corp = (corporate_proxy or "").replace("http://", "").replace("https://", "").split(":")[0]
        for h in (corp, ssh_host):
            ip = _resolve_host(h)
            if ip:
                exclude.append(ip)

        cfg = self.build_config(
            socks_host="127.0.0.1",
            socks_port=int(socks_port),
            exclude_ips=exclude,
            bypass_hosts=bypass_hosts,
            mtu=mtu,
        )
        config_text = json.dumps(cfg, indent=2)
        if self.running():
            if not force_restart:
                old_text = ""
                if self.config_path.is_file():
                    old_text = self.config_path.read_text(encoding="utf-8")
                if old_text == config_text:
                    self.log(f"TUN already running (pid={self.pid()})")
                    return
            self.log("TUN config changed — перезапуск sing-box")
            self.stop()

        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(config_text, encoding="utf-8")

        self.log(f"Starting TUN (sing-box, mtu={max(1280, min(1500, int(mtu)))}) → socks5://127.0.0.1:{socks_port}")
        if elevate:
            self.log("Нужны права администратора для виртуального адаптера")

        pid = self._launch(exe, elevate=elevate)
        if pid:
            self.pid_path.write_text(str(pid), encoding="utf-8")

        deadline = time.monotonic() + 10.0
        interval = 0.05
        attempt = 0
        while time.monotonic() < deadline:
            if self.running():
                self.log("TUN активен (система → SOCKS → VPS)")
                return
            if attempt % 4 == 0 and self._tun_iface_present():
                found = self._find_sing_box_pid()
                if found:
                    self.pid_path.write_text(str(found), encoding="utf-8")
                    self._pid_scan_at = time.monotonic()
                    self._pid_scan_result = found
                self.log("TUN активен (система → SOCKS → VPS)")
                return
            time.sleep(interval)
            interval = min(interval * 1.3, 0.25)
            attempt += 1
        raise RuntimeError(
            "sing-box не поднял TUN. Нужен admin/sudo (или TUN_ELEVATE=1). "
            f"См. logs/sing-box.log. exe={exe}"
        )

    def stop(self) -> None:
        pid = self.pid()
        if pid:
            procutil.kill_pid(pid)
            self.log(f"sing-box pid={pid} stopped")
        for orphan in procutil.pids_named("sing-box.exe", "sing-box"):
            if orphan != pid:
                procutil.kill_pid(orphan)
        self._pid_scan_at = 0.0
        self._pid_scan_result = None
        self.pid_path.unlink(missing_ok=True)
        self.log("TUN выключен")

    def _launch(self, exe: Path, *, elevate: bool) -> int | None:
        args = [str(exe), "run", "-c", str(self.config_path)]
        if sys.platform == "win32" and elevate and not procutil.is_admin():
            return self._start_elevated_win(exe, self.config_path)

        if sys.platform != "win32" and elevate and hasattr(os, "geteuid") and os.geteuid() != 0:
            return self._start_elevated_linux(args)

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=procutil.creationflags(),
        )
        return proc.pid

    def _start_elevated_win(self, exe: Path, config: Path) -> int | None:
        import ctypes

        params = f'run -c "{config}"'
        rc = int(
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", str(exe), params, str(exe.parent), 0
            )
        )
        if rc <= 32:
            raise RuntimeError(
                f"Не удалось запустить sing-box с UAC (код {rc}). "
                "Запустите от администратора или TUN_ELEVATE=0 от admin-сессии."
            )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            found = self._find_sing_box_pid()
            if found:
                return found
            time.sleep(0.05)
        return None

    def _start_elevated_linux(self, args: list[str]) -> int | None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        for wrapper in (
            ["pkexec", *args],
            ["sudo", "-n", *args],
            ["sudo", *args],
        ):
            try:
                proc = subprocess.Popen(
                    wrapper,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(0.5)
                if proc.poll() is None or self._find_sing_box_pid():
                    self.log(f"TUN via {wrapper[0]}")
                    return proc.pid if proc.poll() is None else self._find_sing_box_pid()
            except FileNotFoundError:
                continue
        raise RuntimeError(
            "Не удалось запустить sing-box с правами root (pkexec/sudo). "
            "Установите polkit или выполните: sudo ops-content tun-on"
        )

    def _find_sing_box_pid(self) -> int | None:
        for pid in procutil.pids_named("sing-box.exe", "sing-box"):
            return pid
        return None

    def _tun_iface_present(self) -> bool:
        if sys.platform == "win32":
            r = procutil.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-NetAdapter -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.Name -like '*ops-content*' -or $_.InterfaceDescription -like '*Wintun*' }).Count",
                ]
            )
            try:
                return int((r.stdout or "0").strip() or "0") > 0
            except ValueError:
                return False
        r = procutil.run(["ip", "link", "show", "ops-content-tun"])
        return r.returncode == 0

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
