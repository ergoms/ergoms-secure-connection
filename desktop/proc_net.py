"""Running processes, Windows services, and their current peers."""

from __future__ import annotations

import ctypes
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop.procutil import run
from desktop.route_tokens import PREFIX_EXE, PREFIX_SVC, is_shared_process

_MAX_PROCS = 400
_MAX_SERVICES = 400
_MAX_PEERS = 48

_IMAGEPATH_RE = re.compile(r'^"([^"]+)"|^(\S+)')


@dataclass(frozen=True)
class ProcessInfo:
    name: str
    pid: int
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "pid": self.pid, "path": self.path, "kind": "process"}


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    display: str
    path: str
    exe: str
    shared: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display": self.display or self.name,
            "path": self.path,
            "exe": self.exe,
            "shared": self.shared,
            "kind": "service",
        }


@dataclass(frozen=True)
class Peer:
    ip: str
    port: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"ip": self.ip, "port": self.port}


def _is_windows() -> bool:
    return sys.platform == "win32"


def _basename(path: str) -> str:
    text = (path or "").strip().strip('"')
    if not text:
        return ""
    return text.replace("\\", "/").split("/")[-1]


def _parse_image_path(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    match = _IMAGEPATH_RE.match(text)
    if not match:
        return text.split()[0] if text.split() else ""
    return (match.group(1) or match.group(2) or "").strip()


def list_processes() -> list[ProcessInfo]:
    if _is_windows():
        found = _list_processes_win()
        if found:
            return found
        return _list_processes_tasklist()
    return _list_processes_linux()


def list_services() -> list[ServiceInfo]:
    if not _is_windows():
        return []
    return _list_services_win()


def resolve_service_image(name: str) -> ServiceInfo | None:
    svc = (name or "").strip()
    if svc.lower().startswith(PREFIX_SVC):
        svc = svc[len(PREFIX_SVC) :].strip()
    if not svc:
        return None
    path = _service_image_path(svc)
    exe = _basename(path)
    display = svc
    if _is_windows():
        display = _service_display_name(svc) or svc
    return ServiceInfo(
        name=svc,
        display=display,
        path=path,
        exe=exe,
        shared=is_shared_process(exe or path),
    )


def collect_peers(*, pid: int = 0, name: str = "", service: str = "") -> list[Peer]:
    pids: list[int] = []
    if pid > 0:
        pids.append(int(pid))
    if service:
        info = resolve_service_image(service)
        if info and info.exe:
            pids.extend(_pids_by_name(info.exe))
    if name:
        raw = name
        if raw.lower().startswith(PREFIX_EXE):
            raw = raw[len(PREFIX_EXE) :].strip()
        pids.extend(_pids_by_name(raw))
        if looks_like_file(raw):
            pids.extend(_pids_by_path(raw))
    uniq = list(dict.fromkeys(p for p in pids if p > 0))
    if not uniq:
        return []
    peers = _tcp_udp_peers(uniq)
    out: list[Peer] = []
    seen: set[str] = set()
    for peer in peers:
        if peer.ip in seen:
            continue
        seen.add(peer.ip)
        out.append(peer)
        if len(out) >= _MAX_PEERS:
            break
    return out


def looks_like_file(raw: str) -> bool:
    text = (raw or "").strip().strip('"')
    return "\\" in text or "/" in text


def _pids_by_name(name: str) -> list[int]:
    want = _basename(name).lower()
    if not want:
        return []
    alt = want if want.endswith(".exe") else f"{want}.exe"
    out: list[int] = []
    for proc in list_processes():
        got = proc.name.lower()
        if got in (want, alt):
            out.append(proc.pid)
    return out


def _pids_by_path(path: str) -> list[int]:
    want = str(Path(path.strip().strip('"')))
    want_l = want.lower()
    out: list[int] = []
    for proc in list_processes():
        if proc.path and proc.path.lower() == want_l:
            out.append(proc.pid)
    return out


def _list_processes_win() -> list[ProcessInfo]:
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return []
    th32cs_snapprocess = 0x00000002
    invalid = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snap == invalid:
        return []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return []
        out: list[ProcessInfo] = []
        seen: set[tuple[str, int]] = set()
        while True:
            name = (entry.szExeFile or "").strip()
            pid = int(entry.th32ProcessID)
            key = (name.lower(), pid)
            if name and pid > 0 and key not in seen:
                seen.add(key)
                out.append(ProcessInfo(name=name, pid=pid, path=_process_path(pid)))
                if len(out) >= _MAX_PROCS:
                    break
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
        out.sort(key=lambda p: (p.name.lower(), p.pid))
        return out
    finally:
        kernel32.CloseHandle(snap)


def _process_path(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = ctypes.c_uint32(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _list_processes_tasklist() -> list[ProcessInfo]:
    proc = run(["tasklist", "/fo", "csv", "/nh"], timeout=8)
    if proc.returncode != 0 or not proc.stdout:
        return []
    out: list[ProcessInfo] = []
    for line in proc.stdout.splitlines():
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 2:
            continue
        name, pid_s = parts[0], parts[1]
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if name and pid > 0:
            out.append(ProcessInfo(name=name, pid=pid))
        if len(out) >= _MAX_PROCS:
            break
    return out


def _list_processes_linux() -> list[ProcessInfo]:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []
    out: list[ProcessInfo] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            exe = ""
            try:
                exe = str((entry / "exe").resolve())
            except OSError:
                exe = ""
        except (OSError, ValueError):
            continue
        if comm:
            out.append(ProcessInfo(name=comm, pid=pid, path=exe))
        if len(out) >= _MAX_PROCS:
            break
    out.sort(key=lambda p: (p.name.lower(), p.pid))
    return out


def _service_image_path(name: str) -> str:
    if not _is_windows():
        return ""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{name}",
        )
        try:
            raw, _ = winreg.QueryValueEx(key, "ImagePath")
        finally:
            key.Close()
        return _parse_image_path(str(raw or ""))
    except OSError:
        return ""


def _service_display_name(name: str) -> str:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{name}",
        )
        try:
            raw, _ = winreg.QueryValueEx(key, "DisplayName")
        except OSError:
            return ""
        finally:
            key.Close()
        return str(raw or "").strip()
    except OSError:
        return ""


def _list_services_win() -> list[ServiceInfo]:
    try:
        import winreg

        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services",
        )
    except OSError:
        return []
    out: list[ServiceInfo] = []
    try:
        index = 0
        while len(out) < _MAX_SERVICES:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                sub = winreg.OpenKey(root, name)
            except OSError:
                continue
            try:
                try:
                    start, _ = winreg.QueryValueEx(sub, "Start")
                except OSError:
                    continue
                if int(start) not in (2, 3):  # AUTO / DEMAND
                    continue
                try:
                    raw, _ = winreg.QueryValueEx(sub, "ImagePath")
                except OSError:
                    continue
                path = _parse_image_path(str(raw or ""))
                if not path:
                    continue
                try:
                    display, _ = winreg.QueryValueEx(sub, "DisplayName")
                except OSError:
                    display = name
                exe = _basename(path)
                out.append(
                    ServiceInfo(
                        name=name,
                        display=str(display or name),
                        path=path,
                        exe=exe,
                        shared=is_shared_process(exe or path),
                    )
                )
            finally:
                sub.Close()
    finally:
        root.Close()
    out.sort(key=lambda s: (s.display.lower(), s.name.lower()))
    return out


def _tcp_udp_peers(pids: list[int]) -> list[Peer]:
    if _is_windows():
        found = _peers_win(pids)
        if found:
            return found
        return _peers_netstat(pids)
    return _peers_netstat(pids)


def _skip_ip(ip: str) -> bool:
    text = (ip or "").strip().strip("[]")
    if not text:
        return True
    if text in ("0.0.0.0", "::", "::1", "127.0.0.1"):
        return True
    if text.startswith("127.") or text.startswith("255."):
        return True
    return False


def _peers_win(pids: list[int]) -> list[Peer]:
    want = set(pids)
    out: list[Peer] = []
    try:
        iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return []
    out.extend(_tcp_table(iphlpapi, want, ipv6=False))
    out.extend(_tcp_table(iphlpapi, want, ipv6=True))
    out.extend(_udp_table(iphlpapi, want, ipv6=False))
    return out


def _dword_ip(value: int) -> str:
    raw = int(value) & 0xFFFFFFFF
    return f"{raw & 0xFF}.{(raw >> 8) & 0xFF}.{(raw >> 16) & 0xFF}.{(raw >> 24) & 0xFF}"


def _port_from_dword(value: int) -> int:
    raw = int(value) & 0xFFFF
    return ((raw & 0xFF) << 8) | (raw >> 8)


def _tcp_table(iphlpapi: Any, want: set[int], *, ipv6: bool) -> list[Peer]:
    af = 23 if ipv6 else 2
    tcp_table_owner_pid_connections = 5
    size = ctypes.c_ulong(0)
    iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, af, tcp_table_owner_pid_connections, 0
    )
    if size.value <= 0:
        return []
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), False, af, tcp_table_owner_pid_connections, 0
    ):
        return []
    count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0]
    if ipv6:
        return _parse_tcp6(buf, count, want)
    return _parse_tcp4(buf, count, want)


def _parse_tcp4(buf: ctypes.Array[ctypes.c_char], count: int, want: set[int]) -> list[Peer]:
    class Row(ctypes.Structure):
        _fields_ = [
            ("dwState", ctypes.c_uint32),
            ("dwLocalAddr", ctypes.c_uint32),
            ("dwLocalPort", ctypes.c_uint32),
            ("dwRemoteAddr", ctypes.c_uint32),
            ("dwRemotePort", ctypes.c_uint32),
            ("dwOwningPid", ctypes.c_uint32),
        ]

    rows = ctypes.cast(
        ctypes.addressof(buf) + ctypes.sizeof(ctypes.c_ulong),
        ctypes.POINTER(Row),
    )
    out: list[Peer] = []
    for i in range(int(count)):
        row = rows[i]
        if int(row.dwOwningPid) not in want:
            continue
        ip = _dword_ip(row.dwRemoteAddr)
        if _skip_ip(ip):
            continue
        out.append(Peer(ip=ip, port=_port_from_dword(row.dwRemotePort)))
    return out


def _parse_tcp6(buf: ctypes.Array[ctypes.c_char], count: int, want: set[int]) -> list[Peer]:
    class Row(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16),
            ("dwLocalScopeId", ctypes.c_uint32),
            ("dwLocalPort", ctypes.c_uint32),
            ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", ctypes.c_uint32),
            ("dwRemotePort", ctypes.c_uint32),
            ("dwState", ctypes.c_uint32),
            ("dwOwningPid", ctypes.c_uint32),
        ]

    rows = ctypes.cast(
        ctypes.addressof(buf) + ctypes.sizeof(ctypes.c_ulong),
        ctypes.POINTER(Row),
    )
    out: list[Peer] = []
    for i in range(int(count)):
        row = rows[i]
        if int(row.dwOwningPid) not in want:
            continue
        ip = _format_ipv6(bytes(row.ucRemoteAddr))
        if _skip_ip(ip):
            continue
        out.append(Peer(ip=ip, port=_port_from_dword(row.dwRemotePort)))
    return out


def _udp_table(iphlpapi: Any, want: set[int], *, ipv6: bool) -> list[Peer]:
    # UDP has no remote peer; skip.
    return []


def _format_ipv6(raw: bytes) -> str:
    if len(raw) != 16:
        return ""
    parts = [f"{raw[i] << 8 | raw[i + 1]:x}" for i in range(0, 16, 2)]
    return ":".join(parts)


def _peers_netstat(pids: list[int]) -> list[Peer]:
    want = {str(p) for p in pids}
    args = ["netstat", "-ano"] if _is_windows() else ["netstat", "-anp"]
    try:
        proc = run(args, timeout=8)
    except Exception:  # noqa: BLE001
        return []
    if proc.returncode not in (0, 1) or not proc.stdout:
        return []
    out: list[Peer] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[0].upper() not in ("TCP", "TCPV6", "UDP", "UDPV6"):
            continue
        remote = parts[2] if _is_windows() else (parts[4] if len(parts) > 4 else "")
        pid_s = parts[-1]
        if _is_windows():
            if pid_s not in want:
                continue
        else:
            pid_part = pid_s.split("/")[0]
            if pid_part not in want:
                continue
            remote = parts[4] if len(parts) > 4 else parts[3]
        ip, port = _split_endpoint(remote)
        if _skip_ip(ip):
            continue
        out.append(Peer(ip=ip, port=port))
    return out


def _split_endpoint(raw: str) -> tuple[str, int]:
    text = (raw or "").strip()
    if not text or text.startswith("*") or text == "0.0.0.0:0":
        return "", 0
    if text.startswith("["):
        host, _, port_s = text[1:].partition("]:")
        try:
            return host, int(port_s)
        except ValueError:
            return host, 0
    if text.count(":") == 1:
        host, _, port_s = text.partition(":")
        try:
            return host, int(port_s)
        except ValueError:
            return host, 0
    return text, 0
