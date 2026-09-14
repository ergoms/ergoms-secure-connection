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
from desktop.logutil import noop
from desktop.net_host import resolve_host
from lib.netutil import port_open

LogFn = Callable[[str], None]

STATE_NAME = "kill-switch.json"
BLACKHOLE_V4 = (
    ("0.0.0.0", "128.0.0.0"),
    ("128.0.0.0", "128.0.0.0"),
)
# 127.0.0.1 as next-hop is rejected on modern Windows; on-link via loopback works.
BLACKHOLE_GW = "0.0.0.0"
BLACKHOLE_METRIC = 512
_SKIP_GW = frozenset({"on-link", "0.0.0.0", "127.0.0.1", "::", "::1"})
_APPLIED_TTL = 10.0
_ROUTE_PRINT_TTL = 0.8
_applied_cache: tuple[float, bool] | None = None
_route_print_cache: tuple[float, str] | None = None
_WIN_BLACKHOLE_LO = re.compile(
    r"0\.0\.0\.0\s+128\.0\.0\.0\s+\S+\s+127\.0\.0\.1",
    re.IGNORECASE,
)
_WIN_BLACKHOLE_HI = re.compile(
    r"128\.0\.0\.0\s+128\.0\.0\.0\s+\S+\s+127\.0\.0\.1",
    re.IGNORECASE,
)
_WIN_BLACKHOLE = _WIN_BLACKHOLE_LO


def _host_open(host: str, port: int = 443, timeout: float = 2.5) -> bool:
    return port_open(host, port, timeout=timeout)


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
        ip = resolve_host(host)
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


def _route_print_win(*, force: bool = False) -> str:
    global _route_print_cache
    now = time.monotonic()
    if (
        not force
        and _route_print_cache
        and now - _route_print_cache[0] < _ROUTE_PRINT_TTL
    ):
        return _route_print_cache[1]
    try:
        r = procutil.run(["route", "print", "-4"], timeout=8)
    except (OSError, FileNotFoundError):
        text = ""
    else:
        text = r.stdout or ""
    _route_print_cache = (now, text)
    return text


def _gateway_win(dest: str) -> str | None:
    text = _route_print_win()
    if not text:
        return None
    prefer: set[str] = set()
    if dest:
        try:
            from desktop.tun import _win_if_alias, iface_ipv4s

            idx = _iface_index_win(dest)
            name = _win_if_alias(idx) if idx else None
            prefer.update(iface_ipv4s(name or ""))
        except Exception:
            prefer = set()
    best: tuple[int, str] | None = None
    preferred: str | None = None
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
        if prefer and iface in prefer:
            preferred = gw
        if best is None or metric < best[0]:
            best = (metric, gw)
    return preferred or (best[1] if best else None)


def install_commands(
    allow: list[str], *, gw: str | None = None, blackhole: bool = False
) -> list[str]:
    """Host /32 pins + IPv6 block. IPv4 loopback /1 only when blackhole=True."""
    hop = gw or (underlay_gateway(allow[0]) if allow else None)
    if sys.platform == "win32":
        return _cmds_win_install(allow, hop, blackhole=blackhole)
    return _cmds_linux_install(allow, hop, blackhole=blackhole)


def remove_commands(allow: list[str], *, gw: str | None = None) -> list[str]:
    if sys.platform == "win32":
        return _cmds_win_remove(allow, gw)
    return _cmds_linux_remove(allow, gw)


def _iface_index_win(dest: str) -> int | None:
    ip = resolve_host(dest) if dest else None
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
        return int(idx.value)
    except Exception:
        return None


def _win_loopback_index() -> int:
    try:
        from desktop.tun import _win_if_index_by_alias
    except Exception:
        return 1
    for name in ("Loopback Pseudo-Interface 1", "Псевдоинтерфейс петли 1"):
        idx = _win_if_index_by_alias(name)
        if idx:
            return int(idx)
    return 1


def lift_ipv4_blackhole_commands() -> list[str]:
    """Drop loopback /1 so TUN /1 can own the default without a blackhole race."""
    cmds: list[str] = []
    for dest, mask in BLACKHOLE_V4:
        cmds.append(f"route delete {dest} mask {mask} 127.0.0.1")
        cmds.append(f"route delete {dest} mask {mask} {BLACKHOLE_GW}")
    return cmds


def lift_ipv4_blackholes(*, log: LogFn = noop) -> None:
    cmds = lift_ipv4_blackhole_commands()
    if procutil.is_admin():
        _run_lines_now(cmds, ignore_fail=True)
    invalidate_applied_cache()
    log("kill switch: снял чёрные IPv4 /1 с loopback — трафик идёт в TUN")


def _cmds_win_install(
    allow: list[str], gw: str | None, *, blackhole: bool = False
) -> list[str]:
    cmds: list[str] = []
    if_idx = _iface_index_win(allow[0]) if allow else None
    lo = _win_loopback_index()
    if blackhole:
        for dest, mask in BLACKHOLE_V4:
            cmds.append(f"route delete {dest} mask {mask} 127.0.0.1")
            cmds.append(f"route delete {dest} mask {mask} {BLACKHOLE_GW}")
            cmds.append(
                f"route add {dest} mask {mask} {BLACKHOLE_GW} "
                f"metric {BLACKHOLE_METRIC} if {lo}"
            )
    else:
        cmds.extend(lift_ipv4_blackhole_commands())
    if gw:
        for ip in allow:
            line_change = f"route change {ip} mask 255.255.255.255 {gw} metric 1"
            line_add = f"route add {ip} mask 255.255.255.255 {gw} metric 1"
            if if_idx:
                line_change += f" if {if_idx}"
                line_add += f" if {if_idx}"
            cmds.append(line_change)
            cmds.append(line_add)
    cmds.append(
        f"netsh interface ipv6 add route ::/1 interface={lo} metric=1 store=active"
    )
    cmds.append(
        f"netsh interface ipv6 add route 8000::/1 interface={lo} metric=1 store=active"
    )
    return cmds


def _cmds_win_remove(allow: list[str], gw: str | None) -> list[str]:
    del gw
    cmds: list[str] = []
    for dest, mask in BLACKHOLE_V4:
        cmds.append(f"route delete {dest} mask {mask} 127.0.0.1")
        cmds.append(f"route delete {dest} mask {mask} {BLACKHOLE_GW}")
    for ip in allow:
        cmds.append(f"route delete {ip} mask 255.255.255.255")
    cmds.append("netsh interface ipv6 delete route ::/1 interface=1")
    cmds.append("netsh interface ipv6 delete route 8000::/1 interface=1")
    return cmds


def _cmds_linux_install(
    allow: list[str], gw: str | None, *, blackhole: bool = False
) -> list[str]:
    cmds = [
        "ip -6 route replace ::/1 dev lo metric 512",
        "ip -6 route replace 8000::/1 dev lo metric 512",
    ]
    if blackhole:
        cmds[0:0] = [
            "ip route replace 0.0.0.0/1 dev lo metric 512",
            "ip route replace 128.0.0.0/1 dev lo metric 512",
        ]
    else:
        cmds[0:0] = [
            "ip route del 0.0.0.0/1 dev lo",
            "ip route del 128.0.0.0/1 dev lo",
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


def invalidate_applied_cache() -> None:
    global _applied_cache, _route_print_cache
    _applied_cache = None
    _route_print_cache = None


def _win_sealed(text: str) -> bool:
    return bool(_WIN_BLACKHOLE_LO.search(text) and _WIN_BLACKHOLE_HI.search(text))


def _win_tun_split(text: str) -> bool:
    return bool(
        re.search(r"0\.0\.0\.0\s+128\.0\.0\.0\s+\S+\s+172\.19\.", text)
        and re.search(r"128\.0\.0\.0\s+128\.0\.0\.0\s+\S+\s+172\.19\.", text)
    )


def is_sealed(*, force: bool = False) -> bool:
    """True only if IPv4 loopback /1 blackholes are present (fail-closed)."""
    if sys.platform == "win32":
        return _win_sealed(_route_print_win(force=force))
    try:
        r = procutil.run(["ip", "-4", "route", "show", "0.0.0.0/1"], timeout=3)
    except (OSError, FileNotFoundError):
        return False
    return "dev lo" in (r.stdout or "")


def is_applied(*, force: bool = False) -> bool:
    global _applied_cache
    now = time.monotonic()
    if (
        not force
        and _applied_cache
        and now - _applied_cache[0] < _APPLIED_TTL
    ):
        return _applied_cache[1]
    result = _is_applied_uncached()
    _applied_cache = (now, result)
    return result


def _is_applied_uncached() -> bool:
    if sys.platform == "win32":
        text = _route_print_win(force=True)
        return _win_sealed(text) or _win_tun_split(text)
    try:
        r = procutil.run(["ip", "-4", "route", "show", "0.0.0.0/1"], timeout=3)
    except (OSError, FileNotFoundError):
        return False
    return "dev lo" in (r.stdout or "")


def remember_plan(var_dir: Path, allow: list[str], *, gw: str | None = None) -> None:
    hop = gw or (underlay_gateway(allow[0]) if allow else "") or ""
    idx = _iface_index_win(allow[0]) if sys.platform == "win32" and allow else None
    _save_state(
        var_dir,
        {
            "allow": list(allow),
            "gw": hop,
            "if_idx": idx,
            "applied": is_applied(),
        },
    )


def planned_pin_commands(var_dir: Path, allow: list[str] | None = None) -> list[str]:
    """Host /32 commands using gw/if captured before TUN auto_route."""
    st = _load_state(var_dir)
    unique = list(
        dict.fromkeys(
            allow or [str(x) for x in (st.get("allow") or []) if x]
        )
    )
    hop = str(st.get("gw") or "") or None
    raw_idx = st.get("if_idx")
    try:
        idx = int(raw_idx) if raw_idx not in (None, "") else None
    except (TypeError, ValueError):
        idx = None
    return pin_commands(unique, gw=hop, if_idx=idx)


def pin_commands(
    allow: list[str], *, gw: str | None = None, if_idx: int | None = None
) -> list[str]:
    """Re-add host /32 routes so TUN auto_route cannot steal VPS/Squid."""
    unique = list(dict.fromkeys(ip for ip in allow if ip))
    hop = gw or (underlay_gateway(unique[0]) if unique else None)
    if not hop or not unique:
        return []
    if sys.platform == "win32":
        idx = if_idx if if_idx else _iface_index_win(unique[0])
        cmds: list[str] = []
        for ip in unique:
            line_change = f"route change {ip} mask 255.255.255.255 {hop} metric 1"
            line_add = f"route add {ip} mask 255.255.255.255 {hop} metric 1"
            if idx:
                line_change += f" if {idx}"
                line_add += f" if {idx}"
            # change-if-exists then add-if-missing — no delete gap that kills QUIC
            cmds.append(line_change)
            cmds.append(line_add)
        return cmds
    return [f"ip route replace {ip}/32 via {hop}" for ip in unique]


def suppress_underlay_ipv6(*, var_dir: Path, log: LogFn = noop) -> None:
    """Stop GitHub/Chrome Happy Eyeballs from skipping TUN over IPv6."""
    if sys.platform != "win32":
        return
    from desktop.tun import underlay_ifaces

    ifaces = underlay_ifaces()
    if not ifaces:
        return
    cmds = [
        f"netsh interface ipv6 set interface {idx} admin=disabled"
        for idx, _name in ifaces
    ]
    if procutil.is_admin():
        _run_lines_now(cmds, ignore_fail=True)
    else:
        _run_privileged_lines(cmds, log=log, ignore_fail=True)
    st = _load_state(var_dir)
    st["ipv6_disabled"] = [idx for idx, _name in ifaces]
    _save_state(var_dir, st)
    log("kill switch: IPv6 выкл на " + ", ".join(name for _i, name in ifaces))


def restore_underlay_ipv6(*, var_dir: Path, log: LogFn = noop) -> None:
    if sys.platform != "win32":
        return
    st = _load_state(var_dir)
    raw = [x for x in (st.get("ipv6_disabled") or []) if x]
    if not raw:
        return
    cmds = [f"netsh interface ipv6 set interface {idx} admin=enabled" for idx in raw]
    if procutil.is_admin():
        _run_lines_now(cmds, ignore_fail=True)
    else:
        _run_privileged_lines(cmds, log=log, ignore_fail=True)
    st["ipv6_disabled"] = []
    _save_state(var_dir, st)
    log("kill switch: IPv6 на underlay включил обратно")


def pin_underlay(
    allow: list[str],
    *,
    var_dir: Path,
    log: LogFn = noop,
    elevate: bool = False,
) -> bool:
    """Restore VPS/Squid /32 after TUN is up. Uses gw/if saved before auto_route."""
    st = _load_state(var_dir)
    unique = list(dict.fromkeys(allow or [str(x) for x in (st.get("allow") or []) if x]))
    hop = str(st.get("gw") or "") or (underlay_gateway(unique[0]) if unique else "")
    raw_idx = st.get("if_idx")
    try:
        idx = int(raw_idx) if raw_idx not in (None, "") else None
    except (TypeError, ValueError):
        idx = None
    cmds = pin_commands(unique, gw=hop or None, if_idx=idx)
    if not cmds:
        log("underlay pin: нет gw/allow — /32 к VPS не закрепляю")
        return False
    log(f"underlay pin: gw={hop} if={idx or '?'} allow={', '.join(unique)}")
    if procutil.is_admin():
        ok = _run_lines_now(cmds, ignore_fail=True)
    elif elevate:
        ok = _run_privileged_lines(cmds, log=log, ignore_fail=True)
    else:
        return False
    invalidate_applied_cache()
    return ok


def apply(
    allow: list[str],
    *,
    var_dir: Path,
    log: LogFn = noop,
    blackhole: bool = False,
) -> bool:
    """Pin VPS/Squid. IPv4 loopback /1 only when blackhole=True (fail-closed)."""
    unique = list(dict.fromkeys(allow))
    if is_applied() and not blackhole:
        hop = underlay_gateway(unique[0]) if unique else underlay_gateway("")
        idx = _iface_index_win(unique[0]) if sys.platform == "win32" and unique else None
        _save_state(
            var_dir,
            {"allow": unique, "gw": hop or "", "if_idx": idx, "applied": True},
        )
        log("kill switch: маршруты уже стоят")
        suppress_underlay_ipv6(var_dir=var_dir, log=log)
        return True
    gw = underlay_gateway(unique[0]) if unique else underlay_gateway("")
    idx = _iface_index_win(unique[0]) if sys.platform == "win32" and unique else None
    if not gw:
        log("kill switch: нет default gateway — OS-маршруты не ставлю (останется strict_route)")
        _save_state(
            var_dir, {"allow": unique, "gw": "", "if_idx": idx, "applied": False}
        )
        return False
    reachable_before = _host_open(unique[0], 443) if unique else False
    cmds = install_commands(unique, gw=gw, blackhole=blackhole)
    log(f"kill switch: gw={gw} allow={', '.join(unique) or 'нет'}")
    _run_privileged_lines(
        cmds, log=log, expect_applied=True if blackhole else None
    )
    invalidate_applied_cache()
    present = is_applied(force=True)
    _save_state(
        var_dir, {"allow": unique, "gw": gw, "if_idx": idx, "applied": present}
    )
    text = _route_print_win(force=True) if sys.platform == "win32" else ""
    for ip in unique:
        hit = [ln.strip() for ln in text.splitlines() if ip in ln]
        if hit:
            log(f"kill switch: маршрут {ip}: {hit[0][:120]}")
        else:
            log(f"kill switch: в таблице нет {ip}/32 — путь к VPS может быть перекрыт")
    if unique and reachable_before and not _host_open(unique[0], 443):
        log("kill switch: VPS :443 пропал после маршрутов — снимаю OS-kill-switch")
        clear(var_dir=var_dir, log=log)
        return False
    host_ok = any(ip in text for ip in unique)
    if blackhole and present:
        log("kill switch: OK — интернет закрыт (чёрные /1), кроме VPS")
        _save_state(
            var_dir, {"allow": unique, "gw": gw, "if_idx": idx, "applied": True}
        )
        return True
    if not blackhole and host_ok:
        log("kill switch: VPS закреплён, чёрные IPv4 /1 не ставлю — их глушил браузер")
        _save_state(
            var_dir, {"allow": unique, "gw": gw, "if_idx": idx, "applied": True}
        )
        suppress_underlay_ipv6(var_dir=var_dir, log=log)
        return True
    if procutil.is_admin():
        log("kill switch: команды выполнены, но маршруты не видны")
    else:
        log("kill switch: не удалось поставить OS-маршруты (нужны права)")
    return present or host_ok


def clear(*, var_dir: Path, log: LogFn = noop) -> None:
    restore_underlay_ipv6(var_dir=var_dir, log=log)
    st = _load_state(var_dir)
    allow = [str(x) for x in (st.get("allow") or []) if x]
    gw = str(st.get("gw") or "") or None
    cmds = remove_commands(allow, gw=gw)
    _run_privileged_lines(cmds, log=log, ignore_fail=True, expect_applied=False)
    invalidate_applied_cache()
    try:
        state_path(var_dir).unlink(missing_ok=True)
    except OSError:
        pass
    still = is_applied(force=True)
    if still:
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
    cleaned = [ln.strip() for ln in lines if ln and ln.strip()]
    if not cleaned:
        return True
    ok = True
    for line in cleaned:
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
        if r.returncode == 0 or ignore_fail:
            continue
        err = (r.stderr or "") + (r.stdout or "")
        if _benign_route_error(err):
            continue
        if args[:2] == ["route", "delete"] or args[:3] == ["ip", "route", "del"]:
            continue
        if "netsh" in args[0] and ("delete" in args or "add" in args):
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
        if is_applied(force=True) == expect_applied:
            return True
        time.sleep(0.2)
    return is_applied(force=True) == expect_applied if expect_applied is not None else True


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
