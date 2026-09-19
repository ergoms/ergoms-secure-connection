"""Subprocess helpers that do not flash console windows on Windows."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

_PROC_CACHE_TTL = 1.0
_proc_cache: dict[tuple, tuple[float, list[int]]] = {}
_CMD_SUFFIXES = {".cmd", ".bat", ".com"}


def creationflags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


def startupinfo() -> subprocess.STARTUPINFO | None:
    """Hide console windows even when a .cmd shim slips through."""
    if sys.platform != "win32":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    info.wShowWindow = 0
    return info


def which_exe(name: str, extra: Sequence[str | Path] | None = None) -> str | None:
    """Like shutil.which, but prefer a real .exe over git.cmd / docker.cmd.

    CREATE_NO_WINDOW does not hide the flash from cmd.exe script wrappers.
    """
    found = shutil.which(name)
    candidates: list[Path] = []
    if found:
        candidates.append(Path(found))
    if extra:
        candidates.extend(Path(p) for p in extra if p)
    if sys.platform != "win32":
        return found
    seen: set[str] = set()
    fallback: str | None = found
    for raw in candidates:
        paths = [raw]
        if raw.suffix.lower() in _CMD_SUFFIXES:
            paths.insert(0, raw.with_suffix(".exe"))
        for cand in paths:
            key = str(cand).lower()
            if key in seen:
                continue
            seen.add(key)
            if not cand.is_file():
                continue
            if cand.suffix.lower() == ".exe":
                return str(cand)
            if fallback is None:
                fallback = str(cand)
    return fallback


def is_admin() -> bool:
    """True when this process already has admin/root (no UAC/sudo needed)."""
    if sys.platform == "win32":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False
    return bool(hasattr(os, "geteuid") and os.geteuid() == 0)


def win_quote(arg: str) -> str:
    if not arg or any(ch in arg for ch in ' \t"'):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def linux_root_env() -> list[str]:
    """HOME / data dir of the invoking user — sudo otherwise switches to /root."""
    from desktop.paths import data_root

    home = (os.environ.get("HOME") or "").strip() or str(Path.home())
    user = (
        (os.environ.get("SUDO_USER") or "").strip()
        or (os.environ.get("USER") or "").strip()
        or (os.environ.get("LOGNAME") or "").strip()
    )
    data = (os.environ.get("ERGOMS_SC_DATA") or "").strip() or str(data_root())
    pairs = [f"HOME={home}", f"ERGOMS_SC_DATA={data}"]
    if user:
        pairs.append(f"USER={user}")
        pairs.append(f"LOGNAME={user}")
    for key in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    ):
        val = (os.environ.get(key) or "").strip()
        if val:
            pairs.append(f"{key}={val}")
    return pairs


def relaunch_as_admin(
    args: Sequence[str],
    *,
    cwd: str | None = None,
    show: int = 1,
) -> bool:
    """Start *args* elevated. Caller should exit on True (Windows). Linux execs."""
    if not args:
        return False
    if is_admin():
        return False
    if sys.platform == "win32":
        import ctypes

        file = str(args[0])
        params = " ".join(win_quote(str(a)) for a in args[1:])
        directory = cwd or os.getcwd()
        rc = int(
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", file, params, directory, int(show)
            )
        )
        return rc > 32
    return _relaunch_as_root_linux([str(a) for a in args], cwd=cwd)


def _relaunch_as_root_linux(args: list[str], *, cwd: str | None) -> bool:
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            pass
    launched = ["env", *linux_root_env(), *args]
    sudo = shutil.which("sudo")
    pkexec = shutil.which("pkexec")
    tty = False
    try:
        tty = bool(sys.stdin.isatty())
    except Exception:  # noqa: BLE001
        tty = False
    wrappers: list[tuple[str, list[str]]] = []
    if sudo and tty:
        wrappers.append((sudo, [sudo, *launched]))
    if pkexec:
        wrappers.append((pkexec, [pkexec, *launched]))
    if sudo and not tty:
        wrappers.append((sudo, [sudo, *launched]))
    for file, argv in wrappers:
        try:
            os.execvp(file, argv)
        except OSError:
            continue
    return False


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
    timeout: float | None = 30.0,
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
        startupinfo=startupinfo(),
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
        "startupinfo": startupinfo(),
    }
    # Survive parent exit (CLI `on` returns immediately; bridge must keep running).
    if detached:
        if sys.platform == "win32":
            kw["creationflags"] = creationflags() | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kw["start_new_session"] = True
    return subprocess.Popen(**kw)


def _linux_proc_state(pid: int) -> str:
    """Linux /proc/pid/stat state: R/S/D/Z/X, or empty if unknown."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return ""
    rp = raw.rfind(")")
    if rp < 0:
        return ""
    parts = raw[rp + 1 :].split()
    return parts[0] if parts else ""


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
    from desktop.sys.constants import SINGBOX_PROCESS_NAMES

    return process_basename(pid) in {n.lower() for n in SINGBOX_PROCESS_NAMES}


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
        pid = int(name)
        if pid == os.getpid():
            continue
        if comm not in want:
            continue
        if _linux_proc_state(pid) in {"Z", "X"}:
            continue
        found.append(pid)
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
        except ProcessLookupError:
            alive = False
        except PermissionError:
            # EPERM: the process exists (often root-owned TUN) but we cannot signal it.
            alive = True
        except OSError:
            alive = False
        if alive and _linux_proc_state(pid) in {"Z", "X"}:
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


def _enable_debug_privilege() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.windll.advapi32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(
            kernel32.GetCurrentProcess(), 0x20 | 0x8, ctypes.byref(token)
        ):
            return
        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        luid = LUID()
        if not advapi.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            kernel32.CloseHandle(token)
            return
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = 0x2
        advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        kernel32.CloseHandle(token)
    except Exception:
        pass


def _terminate_win(pid: int) -> bool:
    """TerminateProcess; True if the call was issued (process may still be dying)."""
    try:
        import ctypes

        _enable_debug_privilege()
        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, 0, int(pid))
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def kill_pid(pid: int) -> bool:
    """Best-effort kill. Returns True if the process is gone."""
    if pid <= 0 or pid == os.getpid():
        return True
    invalidate_proc_cache()
    if sys.platform == "win32":
        _terminate_win(pid)
        if _wait_pid_dead(pid, timeout=3.0):
            return True
        run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return _wait_pid_dead(pid, timeout=1.0)
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    if _wait_pid_dead(pid, timeout=1.2):
        return True
    _linux_drop_tun_dev()
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    try:
        os.killpg(pid, 9)
    except OSError:
        pass
    return _wait_pid_dead(pid, timeout=3.0)


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
            None, "runas", "taskkill.exe", params, None, 1
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


def pids_listening_on(
    port: int, host: str = "127.0.0.1", *, cache: bool = True
) -> list[int]:
    """PIDs with a TCP LISTEN socket on host:port (best-effort)."""
    if port <= 0:
        return []
    return pids_listening_on_many([port], host=host, cache=cache).get(int(port), [])


def pids_listening_on_many(
    ports: Sequence[int], host: str = "127.0.0.1", *, cache: bool = True
) -> dict[int, list[int]]:
    """One netstat/ss scan for several listen ports."""
    wanted = [int(p) for p in ports if int(p) > 0]
    out: dict[int, list[int]] = {p: [] for p in wanted}
    if not wanted:
        return out
    pending: list[int] = []
    for port in wanted:
        if cache:
            cached = _proc_cache_get(("listen", port, host))
            if cached is not None:
                out[port] = cached
                continue
        pending.append(port)
    if not pending:
        return out
    found: dict[int, list[int]] = {p: [] for p in pending}
    if sys.platform == "win32":
        r = run(["netstat", "-ano", "-p", "tcp"])
        seen: dict[int, set[int]] = {p: set() for p in pending}
        host_l = host.lower()
        pending_s = {p: f":{p}" for p in pending}
        for line in (r.stdout or "").splitlines():
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[1]
            matched: int | None = None
            for port, suffix in pending_s.items():
                if local_addr.endswith(suffix):
                    matched = port
                    break
            if matched is None:
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
            if pid not in seen[matched]:
                seen[matched].add(pid)
                found[matched].append(pid)
    else:
        for port in pending:
            r = run(["ss", "-ltnp", f"sport = :{port}"])
            for m in re.finditer(r"pid=(\d+)", r.stdout or ""):
                pid = int(m.group(1))
                if pid not in found[port]:
                    found[port].append(pid)
    for port, pids in found.items():
        out[port] = pids
        if cache:
            _proc_cache_set(("listen", port, host), pids)
    return out


def listen_ports_open(
    ports: Sequence[int], host: str = "127.0.0.1", *, cache: bool = True
) -> dict[int, bool]:
    """True if TCP LISTEN exists — even when `ss -p` hides a root-owned pid."""
    wanted = [int(p) for p in ports if int(p) > 0]
    open_map = {p: False for p in wanted}
    if not wanted:
        return open_map
    for port, pids in pids_listening_on_many(wanted, host=host, cache=cache).items():
        if pids:
            open_map[port] = True
    missing = [p for p in wanted if not open_map[p]]
    if missing and sys.platform != "win32":
        for port in missing:
            open_map[port] = _linux_ss_listen(port)
    return open_map


def _linux_ss_listen(port: int) -> bool:
    """`ss -ltn` lists other users' sockets; `-p` (pid) often does not."""
    r = run(["ss", "-ltn", f"sport = :{port}"])
    for line in (r.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        head = text.split(None, 1)[0].lower()
        if head in ("state", "netid"):
            continue
        if "listen" in text.lower():
            return True
    return False


def pids_cmdline_match(substr: str, *, cache: bool = True) -> list[int]:
    """PIDs whose command line contains substr (case-insensitive on Windows)."""
    if not substr:
        return []
    return pids_cmdline_match_many([substr], cache=cache).get(substr, [])


def pids_cmdline_match_many(
    substrs: Sequence[str], *, cache: bool = True
) -> dict[str, list[int]]:
    """One process scan for several command-line needles."""
    needles = [s for s in substrs if s]
    out: dict[str, list[int]] = {s: [] for s in needles}
    if not needles:
        return out
    pending: list[str] = []
    for needle in needles:
        if cache:
            cached = _proc_cache_get(("cmdline", needle.lower()))
            if cached is not None:
                out[needle] = cached
                continue
        pending.append(needle)
    if not pending:
        return out
    found = _scan_cmdline_many(pending)
    for needle in pending:
        pids = found.get(needle, [])
        out[needle] = pids
        if cache:
            _proc_cache_set(("cmdline", needle.lower()), pids)
    return out


def _scan_cmdline_many(needles: Sequence[str]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {s: [] for s in needles}
    me = os.getpid()
    if sys.platform == "win32":
        return _scan_cmdline_many_win(needles, me)
    for needle in needles:
        if len(needle) >= 2:
            pattern = f"[{needle[0]}]{needle[1:]}"
        else:
            pattern = needle
        r = run(["pgrep", "-f", pattern])
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != me and pid not in found[needle]:
                    found[needle].append(pid)
    return found


def _read_cmdline_win(pid: int) -> str | None:
    """Read process command line via PEB (x64). None if inaccessible."""
    if sys.platform != "win32" or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_VM_READ = 0x0010
        ProcessBasicInformation = 0

        class PROCESS_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2_0", ctypes.c_void_p),
                ("Reserved2_1", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
            ]

        class UNICODE_STRING(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p),
            ]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ntdll = ctypes.windll.ntdll  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, 0, int(pid)
        )
        if not handle:
            return None
        try:
            pbi = PROCESS_BASIC_INFORMATION()
            status = ntdll.NtQueryInformationProcess(
                handle,
                ProcessBasicInformation,
                ctypes.byref(pbi),
                ctypes.sizeof(pbi),
                None,
            )
            if status != 0 or not pbi.PebBaseAddress:
                return None
            peb = int(pbi.PebBaseAddress)
            params_ptr = ctypes.c_void_p()
            nread = ctypes.c_size_t()
            if not kernel32.ReadProcessMemory(
                handle,
                ctypes.c_void_p(peb + 0x20),
                ctypes.byref(params_ptr),
                ctypes.sizeof(params_ptr),
                ctypes.byref(nread),
            ):
                return None
            if not params_ptr.value:
                return None
            us = UNICODE_STRING()
            if not kernel32.ReadProcessMemory(
                handle,
                ctypes.c_void_p(int(params_ptr.value) + 0x70),
                ctypes.byref(us),
                ctypes.sizeof(us),
                ctypes.byref(nread),
            ):
                return None
            if not us.Buffer or us.Length == 0:
                return ""
            buf = ctypes.create_unicode_buffer(us.Length // 2 + 1)
            if not kernel32.ReadProcessMemory(
                handle,
                ctypes.c_void_p(int(us.Buffer)),
                buf,
                us.Length,
                ctypes.byref(nread),
            ):
                return None
            return buf.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def _scan_cmdline_many_win_ps(needles: Sequence[str], me: int) -> dict[str, list[int]]:
    import json

    token = base64.b64encode(
        json.dumps(list(needles), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    ps = (
        f"$needles = @([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{token}')) "
        "| ConvertFrom-Json); "
        "$filter = \"Name='python.exe' OR Name='pythonw.exe' OR Name='py.exe' "
        "OR Name='ERGOMS SECURE CONNECTION.exe' OR Name='ErgomsSecureConnection.exe' "
        "OR Name='ssh.exe'\"; "
        "Get-CimInstance Win32_Process -Filter $filter | ForEach-Object { "
        "if (-not $_.CommandLine) { return }; "
        "$cl = $_.CommandLine.ToLowerInvariant(); "
        "foreach ($n in $needles) { "
        "$ns = [string]$n; "
        "if ($cl.Contains($ns.ToLowerInvariant())) { "
        "Write-Output (($_.ProcessId.ToString()) + \"`t\" + $ns) "
        "} } }"
    )
    r = run(["powershell", "-NoProfile", "-Command", ps])
    found: dict[str, list[int]] = {s: [] for s in needles}
    alias = {s.lower(): s for s in needles}
    for line in (r.stdout or "").splitlines():
        if "\t" not in line:
            continue
        pid_s, _, needle = line.partition("\t")
        pid_s = pid_s.strip()
        key = alias.get(needle.strip().lower())
        if not key or not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid != me and pid not in found[key]:
            found[key].append(pid)
    return found


def _scan_cmdline_many_win(needles: Sequence[str], me: int) -> dict[str, list[int]]:
    names = {
        "python.exe",
        "pythonw.exe",
        "py.exe",
        "ergomssecureconnection.exe",
        "ssh.exe",
    }
    pids = _pids_named_win(names)
    found: dict[str, list[int]] = {s: [] for s in needles}
    lower_needles = [(s, s.lower()) for s in needles]
    any_read = False
    for pid in pids:
        if pid == me:
            continue
        cl = _read_cmdline_win(pid)
        if cl is None:
            continue
        any_read = True
        cl_l = cl.lower()
        for orig, low in lower_needles:
            if low in cl_l and pid not in found[orig]:
                found[orig].append(pid)
    if not any_read and pids:
        return _scan_cmdline_many_win_ps(needles, me)
    return found


_TUN_BIN_NAMES = (
    "ergoms-tun.exe",
    "ergoms-tun",
    "ergoms-tun-awg.exe",
    "ergoms-tun-awg",
    "sing-box.exe",
    "sing-box",
    "sing-box-awg",
)


def tun_bin_pids() -> list[int]:
    return pids_named(*_TUN_BIN_NAMES)


def _linux_drop_tun_dev() -> None:
    """Delete the TUN iface so a D-state sing-box can die after SIGKILL."""
    if sys.platform == "win32":
        return
    try:
        from desktop.net._impl import LINUX_TUN_IFACE_NAME as name
    except Exception:  # noqa: BLE001
        name = "ergoms-tun"
    try:
        run(["ip", "link", "delete", "dev", name], timeout=8)
    except (OSError, FileNotFoundError):
        pass


def _linux_pkill_tun() -> None:
    if sys.platform == "win32":
        return
    for name in ("sing-box", "sing-box-awg", "ergoms-tun", "ergoms-tun-awg"):
        try:
            run(["pkill", "-9", "-x", name], timeout=5)
        except (OSError, FileNotFoundError):
            continue


def _taskkill_images() -> None:
    if sys.platform != "win32":
        return
    run(["taskkill", "/F", "/IM", "ergoms-tun.exe"], timeout=8)
    run(["taskkill", "/F", "/IM", "ergoms-tun-awg.exe"], timeout=8)
    run(["taskkill", "/F", "/IM", "sing-box.exe"], timeout=8)


def kill_tun_binaries(*, elevate_if_needed: bool = False) -> bool:
    """Kill ergoms-tun / sing-box. Elevate once only if this process is not admin."""
    invalidate_proc_cache()
    if sys.platform == "win32":
        _enable_debug_privilege()
        _taskkill_images()
        leftover = tun_bin_pids()
        if leftover:
            for pid in leftover:
                _terminate_win(pid)
            leftover = [p for p in leftover if pid_alive(p)]
        if leftover and elevate_if_needed and not is_admin():
            return _elevate_kill_tun_win()
        return not tun_bin_pids()
    _linux_drop_tun_dev()
    leftover = tun_bin_pids()
    for pid in leftover:
        kill_pid(pid)
    leftover = [p for p in leftover if pid_alive(p)]
    if leftover:
        _linux_pkill_tun()
        leftover = [p for p in tun_bin_pids() if pid_alive(p)]
    if leftover and elevate_if_needed and not is_admin():
        return elevate_kill_pids(leftover)
    leftover = [p for p in tun_bin_pids() if pid_alive(p)]
    if leftover:
        _linux_drop_tun_dev()
        time.sleep(0.3)
        leftover = [p for p in tun_bin_pids() if pid_alive(p)]
        for pid in leftover:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    return not tun_bin_pids()


def _elevate_kill_tun_win() -> bool:
    """UAC must be visible — hidden runas often does nothing on Windows."""
    import ctypes
    from pathlib import Path

    script = Path(os.environ.get("TEMP") or ".") / "ergoms-stop-tun.cmd"
    script.write_text(
        "@echo off\r\n"
        "taskkill /F /IM ergoms-tun.exe >nul 2>&1\r\n"
        "taskkill /F /IM ergoms-tun-awg.exe >nul 2>&1\r\n"
        "taskkill /F /IM sing-box.exe >nul 2>&1\r\n",
        encoding="utf-8",
    )
    rc = int(
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", "cmd.exe", f'/c "{script}"', None, 1
        )
    )
    if rc <= 32:
        return False
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        if not tun_bin_pids():
            return True
        time.sleep(0.2)
    return not tun_bin_pids()


def kill_pids(
    pids: Sequence[int], *, exclude: int = 0, elevate_if_needed: bool = False
) -> list[int]:
    """Kill unique PIDs; return those that actually died."""
    invalidate_proc_cache()
    targeted: list[int] = []
    seen: set[int] = set()
    for pid in pids:
        if pid <= 0 or pid == exclude or pid == os.getpid() or pid in seen:
            continue
        seen.add(pid)
        if pid_alive(pid):
            targeted.append(pid)
    if sys.platform == "win32":
        _enable_debug_privilege()
        for pid in targeted:
            _terminate_win(pid)
            run(["taskkill", "/F", "/PID", str(pid)], timeout=8)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not any(pid_alive(p) for p in targeted):
                break
            time.sleep(0.05)
    else:
        for pid in targeted:
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
    from lib.netutil import port_open

    def default_probe() -> bool:
        return port_open(host, port, timeout=0.2)

    check = probe or default_probe
    deadline = time.monotonic() + timeout
    interval = interval_start
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
        interval = min(interval * 1.5, interval_max)
    return False
