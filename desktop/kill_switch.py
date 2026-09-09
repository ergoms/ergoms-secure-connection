"""OS kill switch: blackhole default routes except VPS/Squid.

While TUN is up, sing-box `strict_route` drops leaks. These routes stay in the
OS table when sing-box dies, so traffic cannot fall back to the underlay NIC.
Cleared only on an explicit `off`.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

from desktop import procutil
from desktop.tun import _resolve_host

LogFn = Callable[[str], None]

STATE_NAME = "kill-switch.json"
BLACKHOLE_V4 = (
    ("0.0.0.0", "128.0.0.0"),
    ("128.0.0.0", "128.0.0.0"),
)
BLACKHOLE_GW = "127.0.0.1"
BLACKHOLE_METRIC = 512
_SKIP_GW = frozenset({"on-link", "0.0.0.0", "127.0.0.1", "::", "::1"})


def _noop(_msg: str) -> None:
    pass


def state_path(var_dir: Path) -> Path:
    return var_dir / STATE_NAME


def _load_state(var_dir: Path) -> dict:
    path = state_path(var_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(var_dir: Path, data: dict) -> None:
    var_dir.mkdir(parents=True, exist_ok=True)
    state_path(var_dir).write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def allow_ips(*hosts: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in hosts:
        host = (raw or "").strip()
        if not host:
            continue
        ip = _resolve_host(host)
        if not ip or ip in seen:
            continue
        seen.add(ip)
        out.append(ip)
    return out


def underlay_gateway(dest: str) -> str | None:
    dest = (dest or "").strip()
    if sys.platform == "win32":
        return _gateway_win(dest)
    return _gateway_linux(dest)


def _gateway_linux(dest: str) -> str | None:
    target = dest or "1.1.1.1"
    try:
        r = procutil.run(["ip", "-4", "route", "get", target], timeout=3)
    except (OSError, FileNotFoundError):
        return None
    m = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", r.stdout or "")
    if m and m.group(1) not in _SKIP_GW:
        return m.group(1)
    try:
        r = procutil.run(["ip", "-4", "route", "show", "default"], timeout=3)
    except (OSError, FileNotFoundError):
        return None
    for m in re.finditer(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", r.stdout or ""):
        gw = m.group(1)
        if gw not in _SKIP_GW:
            return gw
    return None


def _gateway_win(dest: str) -> str | None:
    del dest
    try:
        r = procutil.run(["route", "print", "-4"], timeout=8)
    except (OSError, FileNotFoundError):
        return None
    text = r.stdout or ""
    best: tuple[int, str] | None = None
    for raw in text.splitlines():
        m = re.search(
            r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\S+)\s+(\S+)\s+(\d+)\s*$",
            raw,
        )
        if not m:
            continue
        gw = m.group(1).strip()
        iface = m.group(2).strip()
        try:
            metric = int(m.group(3))
        except ValueError:
            continue
        if gw.lower() in _SKIP_GW or not re.match(r"\d+\.\d+\.\d+\.\d+$", gw):
            continue
        if iface.startswith("127.") or iface.startswith("172.19."):
            continue
        if best is None or metric < best[0]:
            best = (metric, gw)
    return best[1] if best else None


def install_commands(allow: list[str], *, gw: str | None = None) -> list[str]:
    """Privileged shell lines that install blackhole + host routes."""
    hop = gw or (underlay_gateway(allow[0]) if allow else None)
    if sys.platform == "win32":
        return _cmds_win_install(allow, hop)
    return _cmds_linux_install(allow, hop)


def remove_commands(allow: list[str], *, gw: str | None = None) -> list[str]:
    if sys.platform == "win32":
        return _cmds_win_remove(allow, gw)
    return _cmds_linux_remove(allow, gw)


def _cmds_win_install(allow: list[str], gw: str | None) -> list[str]:
    cmds: list[str] = []
    for dest, mask in BLACKHOLE_V4:
        cmds.append(f"route delete {dest} mask {mask} {BLACKHOLE_GW}")
        cmds.append(
            f"route add {dest} mask {mask} {BLACKHOLE_GW} metric {BLACKHOLE_METRIC}"
        )
    if gw:
        for ip in allow:
            cmds.append(f"route delete {ip} mask 255.255.255.255")
            cmds.append(f"route add {ip} mask 255.255.255.255 {gw} metric 1")
    cmds.append("netsh interface ipv6 add route ::/1 interface=1 metric=512 store=active")
    cmds.append(
        "netsh interface ipv6 add route 8000::/1 interface=1 metric=512 store=active"
    )
    return cmds


def _cmds_win_remove(allow: list[str], gw: str | None) -> list[str]:
    del gw
    cmds: list[str] = []
    for dest, mask in BLACKHOLE_V4:
        cmds.append(f"route delete {dest} mask {mask} {BLACKHOLE_GW}")
    for ip in allow:
        cmds.append(f"route delete {ip} mask 255.255.255.255")
    cmds.append("netsh interface ipv6 delete route ::/1 interface=1")
    cmds.append("netsh interface ipv6 delete route 8000::/1 interface=1")
    return cmds


def _cmds_linux_install(allow: list[str], gw: str | None) -> list[str]:
    cmds = [
        "ip route replace 0.0.0.0/1 dev lo metric 512",
        "ip route replace 128.0.0.0/1 dev lo metric 512",
        "ip -6 route replace ::/1 dev lo metric 512",
        "ip -6 route replace 8000::/1 dev lo metric 512",
    ]
    if gw:
        for ip in allow:
            cmds.append(f"ip route replace {ip}/32 via {gw}")
    return cmds


def _cmds_linux_remove(allow: list[str], gw: str | None) -> list[str]:
    del gw
    cmds = [
        "ip route del 0.0.0.0/1 dev lo",
        "ip route del 128.0.0.0/1 dev lo",
        "ip -6 route del ::/1 dev lo",
        "ip -6 route del 8000::/1 dev lo",
    ]
    for ip in allow:
        cmds.append(f"ip route del {ip}/32")
    return cmds


def is_applied() -> bool:
    if sys.platform == "win32":
        try:
            r = procutil.run(["route", "print", "-4"], timeout=8)
        except (OSError, FileNotFoundError):
            return False
        text = r.stdout or ""
        return bool(
            re.search(r"0\.0\.0\.0\s+128\.0\.0\.0\s+127\.0\.0\.1", text)
        )
    try:
        r = procutil.run(["ip", "-4", "route", "show", "0.0.0.0/1"], timeout=3)
    except (OSError, FileNotFoundError):
        return False
    return "dev lo" in (r.stdout or "")


def remember_plan(var_dir: Path, allow: list[str], *, gw: str | None = None) -> None:
    hop = gw or (underlay_gateway(allow[0]) if allow else "") or ""
    _save_state(var_dir, {"allow": list(allow), "gw": hop, "applied": is_applied()})


def apply(allow: list[str], *, var_dir: Path, log: LogFn = _noop) -> bool:
    """Install routes. Returns True if blackhole is present afterwards."""
    unique = list(dict.fromkeys(allow))
    gw = underlay_gateway(unique[0]) if unique else underlay_gateway("")
    if not gw:
        log("kill switch: нет default gateway — OS-маршруты не ставлю (останется strict_route)")
        _save_state(var_dir, {"allow": unique, "gw": "", "applied": False})
        return False
    cmds = install_commands(unique, gw=gw)
    ok = _run_privileged_lines(cmds, log=log, expect_applied=True)
    present = is_applied()
    _save_state(var_dir, {"allow": unique, "gw": gw, "applied": present})
    if present:
        log(f"kill switch: чёрные маршруты 0.0.0.0/1 + 128.0.0.0/1 (исключения {', '.join(unique) or 'нет'})")
    elif ok:
        log("kill switch: команды выполнены, но маршруты не видны")
    else:
        log("kill switch: не удалось поставить OS-маршруты (нужны права)")
    return present


def clear(*, var_dir: Path, log: LogFn = _noop) -> None:
    st = _load_state(var_dir)
    allow = [str(x) for x in (st.get("allow") or []) if x]
    gw = str(st.get("gw") or "") or None
    cmds = remove_commands(allow, gw=gw)
    _run_privileged_lines(cmds, log=log, ignore_fail=True, expect_applied=False)
    try:
        state_path(var_dir).unlink(missing_ok=True)
    except OSError:
        pass
    if is_applied():
        log("kill switch: чёрные маршруты всё ещё на месте — повторите off от администратора")
    else:
        log("kill switch: OS-маршруты сняты")


def _run_privileged_lines(
    lines: list[str],
    *,
    log: LogFn,
    ignore_fail: bool = False,
    expect_applied: bool | None = None,
) -> bool:
    if not lines:
        return True
    if procutil.is_admin():
        return _run_lines_now(lines, ignore_fail=ignore_fail)
    script = _write_helper_script(lines)
    if sys.platform == "win32":
        return _elevate_win_script(script, log=log, expect_applied=expect_applied)
    return _elevate_linux_script(script, log=log)


def _run_lines_now(lines: list[str], *, ignore_fail: bool) -> bool:
    ok = True
    for line in lines:
        args = _split_cmd(line)
        if not args:
            continue
        try:
            r = procutil.run(args, timeout=8)
        except (OSError, FileNotFoundError):
            ok = False
            if not ignore_fail:
                break
            continue
        if r.returncode != 0 and not ignore_fail and not _benign_route_error(r.stderr or ""):
            # add after delete-miss is fine; real add failure is not
            if args[:2] == ["route", "delete"] or args[:3] == ["ip", "route", "del"]:
                continue
            if "netsh" in args[0] and "delete" in args:
                continue
            if "netsh" in args[0] and "add" in args:
                continue
            ok = False
    return ok


def _benign_route_error(err: str) -> bool:
    low = err.lower()
    return any(
        s in low
        for s in (
            "not found",
            "cannot find",
            "the object already exists",
            "уже существует",
            "не найден",
            "file exists",
            "no such process",
        )
    )


def _split_cmd(line: str) -> list[str]:
    return [p for p in line.split(" ") if p]


def _write_helper_script(lines: list[str]) -> Path:
    from desktop.paths import Paths

    var = Paths().var_dir
    var.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path = var / "kill-switch-run.cmd"
        body = "@echo off\r\n" + "".join(f"{ln} 2>nul\r\n" for ln in lines) + "exit /b 0\r\n"
        path.write_text(body, encoding="utf-8")
        return path
    path = var / "kill-switch-run.sh"
    body = "#!/bin/sh\n" + "".join(f"{ln} || true\n" for ln in lines) + "exit 0\n"
    path.write_text(body, encoding="utf-8")
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _elevate_win_script(
    script: Path, *, log: LogFn, expect_applied: bool | None = None
) -> bool:
    import ctypes

    rc = int(
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", str(script), None, str(script.parent), 0
        )
    )
    if rc <= 32:
        log("kill switch: UAC отклонён")
        return False
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if expect_applied is None:
            time.sleep(0.8)
            return True
        if is_applied() == expect_applied:
            return True
        time.sleep(0.2)
    return is_applied() == expect_applied if expect_applied is not None else True


def _elevate_linux_script(script: Path, *, log: LogFn) -> bool:
    for wrapper in (
        ["pkexec", "sh", str(script)],
        ["sudo", "-n", "sh", str(script)],
        ["sudo", "sh", str(script)],
    ):
        try:
            r = procutil.run(wrapper, timeout=60)
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            log(f"kill switch: {wrapper[0]}: {exc}")
            continue
        if r.returncode == 0:
            return True
    log("kill switch: нужен pkexec/sudo")
    return False
