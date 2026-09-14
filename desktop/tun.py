"""sing-box binary: find, download, stop leftover processes."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from desktop import procutil
from desktop.paths import bundle_dir, is_frozen

LogFn = Callable[[str], None]

SING_BOX_VERSION = "1.11.15"
AWG_SING_BOX_VERSION = "1.13.12-awg2.0"
AWG_SING_BOX_REPO = "spoofi/sing-box-awg"
AWG_ARCHIVE_SHA256 = {
    "windows-amd64": "a0a1912ebc74eca085a6e6c5a20dc91ec29fc66869230f4454342bb4817588c3",
    "linux-amd64": "46e21eb918dbbcb0da6b8e6e98e8496407556f0cd7e66f8be33c4185e60681cf",
}


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


def underlay_bind_info(dest: str) -> tuple[str, str, int]:
    """(iface alias, ipv4, if_index) of the NIC that reaches dest, not TUN."""
    alias = detect_bind_interface(dest) or ""
    ip = ""
    idx = 0
    if alias:
        ips = [
            x
            for x in iface_ipv4s(alias)
            if not x.startswith(("169.254.", "0."))
        ]
        if ips:
            ip = ips[0]
        if sys.platform == "win32":
            idx = int(_win_if_index_by_alias(alias) or 0)
    return alias, ip, idx


def bind_underlay_socket(
    sock: socket.socket, *, ip: str = "", if_index: int = 0
) -> None:
    """Force packets out the underlay NIC even if TUN/KS/Amnezia owns default."""
    if ip:
        try:
            sock.bind((ip, 0))
        except OSError:
            pass
    if sys.platform != "win32" or not if_index:
        return
    opt = getattr(socket, "IP_UNICAST_IF", 31)
    try:
        sock.setsockopt(
            socket.IPPROTO_IP, opt, struct.pack("@I", socket.htonl(int(if_index)))
        )
    except OSError:
        pass


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
        if not dev or _is_tun_iface(dev):
            return None
        return dev
    if sys.platform == "win32":
        return _detect_bind_win(dest)
    return None


def _is_tun_iface(name: str) -> bool:
    low = (name or "").strip().lower()
    return low.startswith(("ops-content", "ergoms-secure-connection", "ergoms"))


TUN_IFACE_NAME = "ergoms-secure-connection-tun"
TUN_ADDR_PREFIX = "172.19."

_FOREIGN_VPN = (
    "amnezia",
    "amn0",
    "awg",
    "amneziawg",
    "outline",
    "wireguard",
    "wintun",
    "nordlynx",
    "openvpn",
    "proton",
    "surfshark",
    "tun2socks",
)
# GUI/tunnel binaries. AmneziaVPN-service stays up while the tunnel is down.
_FOREIGN_PROCS = (
    "AmneziaVPN.exe",
    "AmneziaVPN-service.exe",
    "AmneziaWGTunnel.exe",
    "outline.exe",
    "wireguard.exe",
)
_IFACE_UP = frozenset({"connected", "подключен", "подключено"})


def foreign_vpn_processes() -> list[str]:
    """Third-party VPN front-ends running (informational, never a blocker)."""
    found: list[str] = []
    if sys.platform != "win32":
        return found
    for proc in _FOREIGN_PROCS:
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc}", "/NH"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.lower() in (r.stdout or "").lower():
            tag = proc.replace(".exe", "")
            if tag not in found:
                found.append(tag)
    return found


def foreign_vpn_live() -> list[str]:
    """Foreign tunnel that actually steals routes (adapter or split default)."""
    names = [name for _idx, name in leftover_vpn_ifaces()]
    if leftover_vpn_default_cmds() and not names:
        names.append("split-default")
    return names


def leftover_vpn_ifaces() -> list[tuple[int, str]]:
    """Foreign VPN NICs that are up with a routable IPv4 (tunnel still live)."""
    found: list[tuple[int, str]] = []
    if sys.platform != "win32":
        return found
    try:
        r = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return found
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+\d+\s+\d+\s+(\S+)\s+(.+?)\s*$", line)
        if not m:
            continue
        state = m.group(2).lower()
        name = m.group(3).strip()
        if state not in _IFACE_UP or _is_tun_iface(name):
            continue
        low = name.lower()
        if not any(tag in low for tag in _FOREIGN_VPN):
            continue
        routable = [
            ip
            for ip in iface_ipv4s(name)
            if not ip.startswith(("169.254.", "0."))
        ]
        if routable:
            found.append((int(m.group(1)), name))
    return found


def leftover_vpn_default_cmds() -> list[str]:
    """Remove leftover 0.0.0.0/0 and 0.0.0.0/1 on Amnezia/etc.

    AmneziaWG usually installs split defaults (0.0.0.0/1 + 128.0.0.0/1),
    not a single 0.0.0.0/0 — deleting only /0 leaves QUIC inside their TUN.
    """
    cmds: list[str] = []
    for idx, _name in leftover_vpn_ifaces():
        cmds.append(f"route delete 0.0.0.0 mask 0.0.0.0 if {idx}")
        cmds.append(f"route delete 0.0.0.0 mask 128.0.0.0 if {idx}")
        cmds.append(f"route delete 128.0.0.0 mask 128.0.0.0 if {idx}")
    cmds.extend(foreign_split_default_cmds())
    return list(dict.fromkeys(cmds))


def foreign_split_default_cmds() -> list[str]:
    """Drop 0.0.0.0/1 and 128.0.0.0/1 that are not ours (KS lo / this TUN)."""
    cmds: list[str] = []
    if sys.platform != "win32":
        return cmds
    try:
        r = subprocess.run(
            ["route", "print", "-4"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return cmds
    seen: set[str] = set()
    for raw in (r.stdout or "").splitlines():
        m = re.match(
            r"^\s*(0\.0\.0\.0|128\.0\.0\.0)\s+128\.0\.0\.0\s+(\S+)\s+(\S+)\s+(\d+)\s*$",
            raw,
        )
        if not m:
            continue
        dest, hop, iface = m.group(1), m.group(2), m.group(3)
        if hop.startswith("127.") or iface.startswith("127."):
            continue
        if iface.startswith(TUN_ADDR_PREFIX) or hop.startswith(TUN_ADDR_PREFIX):
            continue
        if hop.lower() in {"on-link", "onlink"}:
            continue
        key = f"{dest}|{hop}"
        if key in seen:
            continue
        seen.add(key)
        cmds.append(f"route delete {dest} mask 128.0.0.0 {hop}")
    return cmds


def wait_tun_iface(*, timeout: float = 20.0) -> int | None:
    """Interface index of this app's TUN, or None if it never appeared."""
    if sys.platform != "win32":
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        idx = _win_if_index_by_alias(TUN_IFACE_NAME)
        if idx:
            return idx
        time.sleep(0.25)
    return None


def _win_if_index_by_alias(alias: str) -> int | None:
    want = (alias or "").strip().lower()
    if not want:
        return None
    try:
        r = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+\d+\s+\d+\s+(\S+)\s+(.+?)\s*$", line)
        if not m:
            continue
        if m.group(2).lower() not in _IFACE_UP:
            continue
        if m.group(3).strip().lower() == want:
            return int(m.group(1))
    return None


def install_tun_split_default(if_idx: int, *, metric: int = 5) -> list[str]:
    """Send 0.0.0.0/1 + 128.0.0.0/1 into our TUN without auto_route."""
    hop = "172.19.0.1"
    return [
        f"route add 0.0.0.0 mask 128.0.0.0 {hop} metric {metric} if {if_idx}",
        f"route add 128.0.0.0 mask 128.0.0.0 {hop} metric {metric} if {if_idx}",
    ]


def iface_ipv4s(alias: str) -> list[str]:
    alias = (alias or "").strip().lower()
    if not alias or sys.platform != "win32":
        return []
    try:
        r = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "addresses", f"name={alias}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[str] = []
    for raw in (r.stdout or "").splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", raw)
        if m and not m.group(1).startswith("127."):
            found.append(m.group(1))
    return found


def gateway_via_dest(dest: str) -> str | None:
    """Default gateway of the NIC that actually reaches dest (not Amnezia)."""
    name = detect_bind_interface(dest)
    ips = set(iface_ipv4s(name or ""))
    if not ips:
        from desktop.kill_switch import underlay_gateway

        return underlay_gateway(dest)
    for raw in default_route_lines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        hop, iface = parts[2], parts[3]
        if iface in ips and hop.lower() not in {"on-link", "onlink", "0.0.0.0"}:
            return hop
    from desktop.kill_switch import underlay_gateway

    return underlay_gateway(dest)


def stale_default_cmds(keep_gw: str) -> list[str]:
    """Drop IPv4 defaults that are not the current underlay gateway.

    Office Ethernet / old TAP often leave 10.x defaults with metric 0.
    Tailscale/Amnezia leave 100.x (CGNAT) persistent rows with 'Default'
    instead of a metric — those used to be ignored and ate Hysteria2 UDP.
    """
    keep = (keep_gw or "").strip()
    cmds: list[str] = []
    seen: set[str] = set()
    if sys.platform != "win32" or not keep:
        return cmds
    try:
        r = subprocess.run(
            ["route", "print", "-4"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return cmds
    for raw in (r.stdout or "").splitlines():
        m = re.match(
            r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\S+)\s+(\S+)(?:\s+(\d+|Default))?\s*$",
            raw,
        )
        if not m:
            continue
        hop = m.group(1).strip()
        iface = m.group(2).strip()
        if hop == keep:
            continue
        if iface.startswith("127.") or iface.startswith(TUN_ADDR_PREFIX):
            continue
        if hop.lower() in {"on-link", "onlink", "0.0.0.0"}:
            continue
        if hop in seen:
            continue
        seen.add(hop)
        cmds.append(f"route delete 0.0.0.0 mask 0.0.0.0 {hop}")
        cmds.append(f"route -p delete 0.0.0.0 mask 0.0.0.0 {hop}")
    return cmds


def default_route_lines() -> list[str]:
    """Short IPv4 default-route rows for diagnostics."""
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["route", "print", "-4"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        out: list[str] = []
        for raw in (r.stdout or "").splitlines():
            if re.match(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+", raw):
                out.append(" ".join(raw.split()))
        return out[:8]
    try:
        r = subprocess.run(
            ["ip", "-4", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()][:8]


def foreign_vpn_adapters() -> list[str]:
    """Other VPN NICs/processes that will fight TUN routes (Amnezia, …)."""
    found: list[str] = []
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            r = None
        if r:
            for line in (r.stdout or "").splitlines():
                low = line.lower()
                if "disconnected" in low or "connected" not in low:
                    continue
                if _is_tun_iface(line):
                    continue
                hit = next((tag for tag in _FOREIGN_VPN if tag in low), None)
                if hit and hit not in found:
                    found.append(hit)
        for proc in _FOREIGN_PROCS:
            try:
                r = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {proc}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.lower() in (r.stdout or "").lower():
                tag = proc.replace(".exe", "")
                if tag not in found:
                    found.append(tag)
        return found
    try:
        r = subprocess.run(
            ["ip", "-o", "link", "show"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    for line in (r.stdout or "").splitlines():
        low = line.lower()
        hit = next((tag for tag in _FOREIGN_VPN if tag in low), None)
        if hit and hit not in found:
            found.append(hit)
    return found


def _detect_bind_win(dest: str) -> str | None:
    """Windows NIC alias (e.g. Wi-Fi) used to reach dest before TUN is up."""
    ip = _resolve_host(dest)
    if not ip:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        dest_n = ctypes.windll.ws2_32.inet_addr(ip.encode("ascii"))  # type: ignore[attr-defined]
        if dest_n == 0xFFFFFFFF:
            return None
        idx = wintypes.DWORD()
        err = ctypes.windll.iphlpapi.GetBestInterface(dest_n, ctypes.byref(idx))  # type: ignore[attr-defined]
        if err or not idx.value:
            return None
        name = _win_if_alias(int(idx.value))
        if name and not _is_tun_iface(name):
            return name
    except Exception:
        return None
    return None


def _win_if_alias(if_index: int) -> str | None:
    try:
        r = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+\d+\s+\d+\s+\S+\s+(.+?)\s*$", line)
        if not m or int(m.group(1)) != if_index:
            continue
        name = m.group(2).strip()
        return name or None
    return None


def _arch_tag() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return "amd64"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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
        # Frozen ERGOMS SECURE CONNECTION.exe: ProxyCommand falls back to PATH pythonw
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
        return "ergoms-tun.exe" if sys.platform == "win32" else "sing-box"

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
            legacy = self.tools_dir / "sing-box.exe"
            branded = self.tools_dir / "ergoms-tun.exe"
            if legacy.is_file() and not branded.is_file():
                try:
                    shutil.copy2(legacy, branded)
                except OSError:
                    candidates.append(legacy)
            candidates.append(branded)
            candidates.append(legacy)
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
        for name in (self._bin_name(), "sing-box.exe", "sing-box"):
            packed = bundle_dir() / "tools" / name
            if self._is_native_sing_box(packed):
                return packed
        return None

    def _materialize_bundled(self) -> Path | None:
        """Copy packed sing-box to writable tools/ (UAC + onefile temp)."""
        src = self._bundled_sing_box()
        if src is None:
            return None
        dest = self.tools_dir / self._bin_name()
        if not is_frozen():
            if dest != src and src.is_file() and not dest.is_file():
                try:
                    shutil.copy2(src, dest)
                    return dest
                except OSError:
                    return src
            return dest if dest.is_file() else src
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
        for orphan in procutil.pids_named(
            "sing-box.exe",
            "sing-box",
            "sing-box-awg",
            "ergoms-tun.exe",
            "ergoms-tun",
            "ergoms-tun-awg.exe",
            "ergoms-tun-awg",
        ):
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
        for pid in procutil.pids_named(
            "sing-box.exe",
            "sing-box",
            "sing-box-awg",
            "ergoms-tun.exe",
            "ergoms-tun",
            "ergoms-tun-awg.exe",
            "ergoms-tun-awg",
        ):
            return pid
        return None

    def ensure_downloaded(self, proxy_url: str | None = None) -> Path:
        """Download sing-box for current OS/arch into tools/."""
        import tarfile
        import zipfile

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
            target = self.tools_dir / self._bin_name()
            self.log(f"Downloading {asset}…")
            self._download_file(url, archive, proxy_url=proxy_url)
            with zipfile.ZipFile(archive, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("sing-box.exe") or name.endswith("ergoms-tun.exe"):
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

    def _awg_bin_name(self) -> str:
        return "ergoms-tun-awg.exe" if sys.platform == "win32" else "sing-box-awg"

    def _awg_version_path(self) -> Path:
        return self.tools_dir / "sing-box-awg.ver"

    def _bundled_awg_sing_box(self) -> Path | None:
        for name in (self._awg_bin_name(), "sing-box-awg.exe", "sing-box-awg"):
            packed = bundle_dir() / "tools" / name
            if self._is_native_sing_box(packed):
                return packed
        return None

    def awg_version_ok(self) -> bool:
        for path in (
            self._awg_version_path(),
            bundle_dir() / "tools" / "sing-box-awg.ver",
        ):
            try:
                if (
                    path.is_file()
                    and path.read_text(encoding="utf-8").strip() == AWG_SING_BOX_VERSION
                ):
                    return True
            except OSError:
                continue
        return False

    def _write_awg_version(self) -> None:
        try:
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            self._awg_version_path().write_text(
                AWG_SING_BOX_VERSION + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    def _materialize_bundled_awg(self) -> Path | None:
        """Copy packed AWG sing-box to writable tools/ (same as official)."""
        src = self._bundled_awg_sing_box()
        if src is None:
            return None
        dest = self.tools_dir / self._awg_bin_name()
        try:
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dest)
            if not self.awg_version_ok():
                bundled_ver = bundle_dir() / "tools" / "sing-box-awg.ver"
                if bundled_ver.is_file():
                    shutil.copy2(bundled_ver, self._awg_version_path())
                else:
                    self._write_awg_version()
        except OSError:
            return dest if dest.is_file() else src
        return dest if dest.is_file() else src

    def find_awg_sing_box(self) -> Path | None:
        name = self._awg_bin_name()
        packed = self._materialize_bundled_awg()
        candidates = [
            packed,
            self.tools_dir / name,
            bundle_dir() / "tools" / name,
        ]
        if sys.platform == "win32":
            candidates.append(self.tools_dir / "sing-box-awg.exe")
        else:
            candidates.append(self.tools_dir / "sing-box-awg")
        seen: set[str] = set()
        for c in candidates:
            if c is None:
                continue
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

    def ensure_awg_downloaded(self, proxy_url: str | None = None) -> Path:
        """Pinned AmneziaWG sing-box fork (does not replace official 1.11.15)."""
        import tarfile
        import zipfile

        existing = self.find_awg_sing_box()
        ver_path = self._awg_version_path()
        if existing and ver_path.is_file():
            try:
                got = ver_path.read_text(encoding="utf-8").strip()
            except OSError:
                got = ""
            if got == AWG_SING_BOX_VERSION:
                return existing
        plat = "windows" if sys.platform == "win32" else "linux"
        arch = _arch_tag()
        key = f"{plat}-{arch}"
        digest = AWG_ARCHIVE_SHA256.get(key)
        if not digest:
            raise RuntimeError(
                f"AmneziaWG sing-box нет для {key}. Нужен amd64. "
                f"Форк {AWG_SING_BOX_REPO} {AWG_SING_BOX_VERSION}"
            )
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        if plat == "windows":
            asset = f"sing-box-{AWG_SING_BOX_VERSION}-windows-{arch}.zip"
        else:
            asset = f"sing-box-{AWG_SING_BOX_VERSION}-linux-{arch}.tar.gz"
        url = (
            f"https://github.com/{AWG_SING_BOX_REPO}/releases/download/"
            f"v{AWG_SING_BOX_VERSION}/{asset}"
        )
        archive = self.tools_dir / asset
        self.log(f"Downloading AWG {asset}…")
        self._download_file(url, archive, proxy_url=proxy_url)
        got_hash = _sha256_file(archive)
        if got_hash != digest:
            archive.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 mismatch for {asset}: {got_hash} (ожидали {digest})"
            )
        target = self.tools_dir / self._awg_bin_name()
        if plat == "windows":
            with zipfile.ZipFile(archive, "r") as zf:
                member = next(
                    (
                        n
                        for n in zf.namelist()
                        if n.endswith("sing-box.exe") or n.endswith("ergoms-tun.exe")
                    ),
                    None,
                )
                if not member:
                    raise RuntimeError("sing-box.exe not found in AWG zip")
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
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
                    raise RuntimeError("sing-box not found in AWG tar.gz")
                f = tf.extractfile(member)
                if not f:
                    raise RuntimeError("cannot extract AWG sing-box")
                with open(target, "wb") as dst:
                    shutil.copyfileobj(f, dst)
            target.chmod(0o755)
        archive.unlink(missing_ok=True)
        try:
            ver_path.write_text(AWG_SING_BOX_VERSION + "\n", encoding="utf-8")
        except OSError:
            pass
        self.log(f"sing-box AWG → {target}")
        return target

    def _download_file(self, url: str, dest: Path, *, proxy_url: str | None = None) -> None:
        import urllib.request
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
