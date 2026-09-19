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
from collections.abc import Callable
from pathlib import Path

from desktop import procutil
from desktop.logutil import noop
from desktop.net_host import resolve_host, underlay_gateway
from desktop.paths import bundle_dir, is_frozen
from desktop.sys.win_net import (
    best_interface_index,
    netsh_has_interface,
    netsh_ipv4_interfaces,
    route_print_v4,
    run_route_lines,
)

LogFn = Callable[[str], None]

SING_BOX_VERSION = "1.11.15"
AWG_SING_BOX_VERSION = "1.13.12-awg2.0"
AWG_SING_BOX_REPO = "spoofi/sing-box-awg"
AWG_ARCHIVE_SHA256 = {
    "windows-amd64": "a0a1912ebc74eca085a6e6c5a20dc91ec29fc66869230f4454342bb4817588c3",
    "linux-amd64": "46e21eb918dbbcb0da6b8e6e98e8496407556f0cd7e66f8be33c4185e60681cf",
}


def _resolve_host(host: str) -> str | None:
    return resolve_host(host)


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


# Linux IFNAMSIZ is 16 (15 usable chars). The Windows alias can be longer.
WIN_TUN_IFACE_NAME = "ergoms-secure-connection-tun"
LINUX_TUN_IFACE_NAME = "ergoms-tun"


def tun_iface_name(*, platform: str | None = None) -> str:
    plat = sys.platform if platform is None else platform
    return WIN_TUN_IFACE_NAME if plat == "win32" else LINUX_TUN_IFACE_NAME


TUN_IFACE_NAME = tun_iface_name()
TUN_ADDR_PREFIX = "172.19."
TUN_LAN_CIDR = "172.19.0.0/16"

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
    for iface in netsh_ipv4_interfaces():
        if not iface.up or _is_tun_iface(iface.name):
            continue
        low = iface.name.lower()
        if not any(tag in low for tag in _FOREIGN_VPN):
            continue
        routable = [
            ip
            for ip in iface_ipv4s(iface.name)
            if not ip.startswith(("169.254.", "0."))
        ]
        if routable:
            found.append((iface.idx, iface.name))
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
    text = route_print_v4()
    if not text:
        return cmds
    seen: set[str] = set()
    for raw in text.splitlines():
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


def apply_tun_iface_mtu(if_idx: int, mtu: int, *, log: LogFn = noop) -> None:
    """Pin the Windows TUN NIC MTU so TCP MSS matches the inner tunnel."""
    if sys.platform != "win32" or not if_idx:
        return
    try:
        size = max(1280, min(1400, int(mtu)))
    except (TypeError, ValueError):
        size = 1280
    procutil.run(
        [
            "netsh",
            "interface",
            "ipv4",
            "set",
            "subinterface",
            str(int(if_idx)),
            f"mtu={size}",
            "store=active",
        ],
        timeout=8,
    )
    log(f"TUN if {if_idx} mtu={size}")


def wait_tun_iface(*, timeout: float = 20.0) -> int | None:
    """Interface index of this app's TUN, or None if it never appeared."""
    if sys.platform == "win32":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            idx = _win_if_index_by_alias(TUN_IFACE_NAME, require_up=True)
            if idx:
                return idx
            time.sleep(0.25)
        return None
    name = tun_iface_name()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = procutil.run(["ip", "-o", "link", "show", "dev", name], timeout=2)
        except (OSError, FileNotFoundError):
            return None
        if int(getattr(r, "returncode", 1) or 1) == 0 and name in (r.stdout or ""):
            return 1
        time.sleep(0.25)
    return None


def _delete_tun_adapter_cmds() -> None:
    for args in (
        ["netsh", "interface", "set", "interface", f"name={TUN_IFACE_NAME}", "admin=disabled"],
        ["netsh", "interface", "delete", "interface", f"name={TUN_IFACE_NAME}"],
    ):
        try:
            procutil.run(args, timeout=8)
        except OSError:
            continue


def _remove_hidden_tun_adapter(*, log: LogFn = noop) -> bool:
    """Hidden Wintun is invisible to `netsh ipv4` and still blocks CreateAdapter."""
    safe = TUN_IFACE_NAME.replace("'", "''")
    ps = (
        f"$a=Get-NetAdapter -Name '{safe}' -IncludeHidden "
        "-ErrorAction SilentlyContinue; "
        "if(-not $a){ exit 2 }; "
        "$a | Remove-NetAdapter -Confirm:$false"
    )
    try:
        r = procutil.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=12,
        )
    except OSError:
        return False
    if int(getattr(r, "returncode", 1) or 1) != 0:
        return False
    log(f"убираю скрытый TUN «{TUN_IFACE_NAME}»")
    return True


def remove_stale_tun_adapter(*, log: LogFn = noop, hidden: bool = False) -> bool:
    """Drop a leftover Wintun NIC so the next start can create TUN.

    `hidden=True` also looks at the L2 table and Get-NetAdapter -IncludeHidden.
    A leftover name blocks Wintun CreateAdapter even when ipv4 netsh is empty.
    """
    if sys.platform != "win32":
        return False
    idx = _win_if_index_by_alias(TUN_IFACE_NAME, require_up=False)
    listed = idx is not None
    if not listed and hidden:
        listed = netsh_has_interface(TUN_IFACE_NAME)
    if idx:
        log(f"убираю зависший TUN «{TUN_IFACE_NAME}» if={idx}")
        _delete_tun_adapter_cmds()
    elif listed:
        log(f"убираю зависший TUN «{TUN_IFACE_NAME}» (без IPv4)")
        _delete_tun_adapter_cmds()
    elif hidden:
        _remove_hidden_tun_adapter(log=log)
    else:
        return False
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if _win_if_index_by_alias(TUN_IFACE_NAME, require_up=False) is None:
            break
        time.sleep(0.15)
    if hidden:
        _remove_hidden_tun_adapter(log=log)
    still = _win_if_index_by_alias(TUN_IFACE_NAME, require_up=False) is not None
    if still:
        log(f"TUN «{TUN_IFACE_NAME}» ещё в системе — CreateAdapter может не успеть")
    return not still


def underlay_ifaces() -> list[tuple[int, int, str]]:
    """Up IPv4 NICs except loopback and our TUN: (if_index, metric, alias)."""
    if sys.platform != "win32":
        return []
    out: list[tuple[int, int, str]] = []
    for iface in netsh_ipv4_interfaces():
        if not iface.up:
            continue
        low = iface.name.lower()
        if "loopback" in low or "петл" in low:
            continue
        if _is_tun_iface(iface.name):
            continue
        out.append((iface.idx, iface.metric, iface.name))
    return out


def _win_if_index_by_alias(alias: str, *, require_up: bool = False) -> int | None:
    """Index of a named NIC. First-time Wintun is often not 'connected' yet."""
    want = (alias or "").strip().lower()
    if not want:
        return None
    fallback: int | None = None
    for iface in netsh_ipv4_interfaces():
        if iface.name.lower() != want:
            continue
        if iface.up:
            return iface.idx
        if fallback is None:
            fallback = iface.idx
    if require_up:
        return None
    return fallback


TUN_SPLIT_HOPS = ("172.19.0.1", "172.19.0.2")


def _shared_tun_lan() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def _machine_tun_seed() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            text = str(guid or "").strip()
            if text:
                return text
        except OSError:
            pass
    return socket.gethostname()


def tun_split_hops() -> tuple[str, str]:
    """Per-machine TUN /30. Shared 172.19.0.1 makes RustDesk punch itself."""
    if _shared_tun_lan():
        return TUN_SPLIT_HOPS
    octet = (int(hashlib.sha256(_machine_tun_seed().encode()).hexdigest(), 16) % 254) + 1
    return f"172.19.{octet}.1", f"172.19.{octet}.2"


def tun_iface_cidr() -> str:
    return f"{tun_split_hops()[0]}/30"
LAN_UNDERLAY_NETS = (
    ("10.0.0.0", "255.0.0.0"),
    ("192.168.0.0", "255.255.0.0"),
)
_VIRTUAL_UNDERLAY = ("vethernet", "hyper-v", "default switch", "wsl")


def _is_virtual_underlay(name: str) -> bool:
    low = (name or "").lower()
    return any(tag in low for tag in _VIRTUAL_UNDERLAY)


def underlay_if_index(gw: str = "") -> int:
    """Physical NIC for office/home LAN — never TUN, never Hyper-V."""
    hop = (gw or underlay_gateway("") or "").strip()
    tun_idx = _win_if_index_by_alias(TUN_IFACE_NAME) or 0
    if hop:
        for raw in default_route_lines():
            parts = raw.split()
            if len(parts) < 4 or parts[2] != hop:
                continue
            iface_ip = parts[3]
            if iface_ip.startswith(TUN_ADDR_PREFIX):
                continue
            for idx, _metric, name in underlay_ifaces():
                if iface_ip in iface_ipv4s(name):
                    return idx
        _alias, _ip, idx = underlay_bind_info(hop)
        if idx and idx != tun_idx:
            return idx
    for idx, _metric, name in underlay_ifaces():
        if not _is_virtual_underlay(name):
            return idx
    return 0


def install_tun_split_default(
    if_idx: int, *, metric: int = 5, hop: str = "172.19.0.1"
) -> list[str]:
    """Send 0.0.0.0/1 + 128.0.0.0/1 into our TUN (same hop as the working build)."""
    gw = hop or "172.19.0.1"
    return [
        f"route delete 0.0.0.0 mask 128.0.0.0 if {if_idx}",
        f"route delete 128.0.0.0 mask 128.0.0.0 if {if_idx}",
        f"route add 0.0.0.0 mask 128.0.0.0 {gw} metric {metric} if {if_idx}",
        f"route add 128.0.0.0 mask 128.0.0.0 {gw} metric {metric} if {if_idx}",
    ]


def lan_underlay_commands(gw: str, if_idx: int) -> list[str]:
    """Keep office/home LAN on the physical NIC — split /1 otherwise steals DNS."""
    cmds: list[str] = []
    hop = (gw or "").strip()
    if not hop or not if_idx:
        return cmds
    for dest, mask in LAN_UNDERLAY_NETS:
        cmds.append(f"route delete {dest} mask {mask}")
        cmds.append(f"route add {dest} mask {mask} {hop} metric 1 if {if_idx}")
    return cmds


def remove_lan_underlay_commands() -> list[str]:
    return [f"route delete {dest} mask {mask}" for dest, mask in LAN_UNDERLAY_NETS]


def ensure_lan_underlay(
    if_idx: int = 0, *, gw: str | None = None, log: LogFn = noop
) -> None:
    """Pin 10/8 and 192.168/16 to the physical NIC, never the TUN index."""
    if sys.platform != "win32":
        return
    hop = (gw or underlay_gateway("") or "").strip()
    if not hop:
        return
    tun_idx = _win_if_index_by_alias(TUN_IFACE_NAME) or 0
    idx = underlay_if_index(hop)
    if not idx and if_idx and if_idx != tun_idx:
        idx = if_idx
    if not idx:
        return
    run_route_lines(lan_underlay_commands(hop, idx))
    log(f"LAN underlay: 10/8 и 192.168/16 via {hop} if={idx}")


def remove_tun_split_default(*, windows: bool | None = None) -> list[str]:
    """Drop our 0.0.0.0/1 + 128.0.0.0/1 after sing-box exits (adapter may linger)."""
    win = sys.platform == "win32" if windows is None else windows
    cmds: list[str] = []
    if win:
        cmds.extend(remove_lan_underlay_commands())
        hops = list(dict.fromkeys([*tun_split_hops(), *TUN_SPLIT_HOPS]))
        for hop in hops:
            cmds.append(f"route delete 0.0.0.0 mask 128.0.0.0 {hop}")
            cmds.append(f"route delete 128.0.0.0 mask 128.0.0.0 {hop}")
        return cmds
    hops = list(dict.fromkeys([*tun_split_hops(), *TUN_SPLIT_HOPS]))
    for hop in hops:
        cmds.append(f"ip route del 0.0.0.0/1 via {hop}")
        cmds.append(f"ip route del 128.0.0.0/1 via {hop}")
    return cmds


def our_tun_split_leftover() -> bool:
    """True if our TUN /1 halves are still in the IPv4 table."""
    if sys.platform != "win32":
        return False
    return any(TUN_ADDR_PREFIX in row for row in tun_split_rows())


def tun_split_rows() -> list[str]:
    """IPv4 /1 rows that steal default (TUN or leftover)."""
    if sys.platform != "win32":
        return []
    out: list[str] = []
    for raw in route_print_v4().splitlines():
        if re.match(r"^\s*(0\.0\.0\.0|128\.0\.0\.0)\s+128\.0\.0\.0\s+", raw):
            out.append(" ".join(raw.split()))
    return out


def _parse_split_row(row: str) -> tuple[str, str, str, str, int] | None:
    parts = row.split()
    if len(parts) < 4:
        return None
    dest, mask, hop, iface = parts[0], parts[1], parts[2], parts[3]
    try:
        metric = int(parts[4]) if len(parts) > 4 else 9999
    except ValueError:
        metric = 9999
    return dest, mask, hop, iface, metric


def tun_owns_default(rows: list[str] | None = None) -> bool:
    """True if both /1 halves go via our TUN and no foreign /1 has a better metric."""
    if rows is None:
        if sys.platform != "win32":
            return False
        rows = tun_split_rows()
    have_lo = have_hi = False
    our_metrics: list[int] = []
    foreign_metrics: list[int] = []
    for row in rows:
        parsed = _parse_split_row(row)
        if not parsed:
            continue
        dest, mask, hop, iface, metric = parsed
        if mask != "128.0.0.0":
            continue
        ours = hop.startswith(TUN_ADDR_PREFIX) or iface.startswith(TUN_ADDR_PREFIX)
        loop = hop.startswith("127.") or iface.startswith("127.")
        if ours:
            if dest == "0.0.0.0":
                have_lo = True
            if dest == "128.0.0.0":
                have_hi = True
            our_metrics.append(metric)
        elif not loop:
            foreign_metrics.append(metric)
    if not (have_lo and have_hi):
        return False
    if not foreign_metrics or not our_metrics:
        return not foreign_metrics
    return min(our_metrics) <= min(foreign_metrics)


def run_route_cmds(cmds: list[str]) -> list[str]:
    """Run `route`/`ip` lines, ignore per-line failures. Returns cmds attempted."""
    for line in cmds:
        args = [p for p in line.split(" ") if p]
        try:
            procutil.run(args, timeout=8)
        except OSError:
            continue
    return list(cmds)


def run_leftover_vpn_default_cmds() -> list[str]:
    """Delete foreign 0.0.0.0/0 and /1. Returns commands that were attempted."""
    return run_route_cmds(leftover_vpn_default_cmds())


def reclaim_tun_default(if_idx: int | None = None) -> bool:
    """Strip foreign defaults and reinstall our /1 split. True if TUN owns default."""
    run_leftover_vpn_default_cmds()
    idx = if_idx or wait_tun_iface(timeout=2.0)
    if idx:
        ensure_tun_split_default(idx)
    return tun_owns_default()


def tun_split_installed(if_idx: int) -> bool:
    """True if both IPv4 /1 halves point at this TUN index or 172.19.*."""
    rows = tun_split_rows()
    lo = hi = False
    token = str(if_idx)
    for row in rows:
        parts = row.split()
        if len(parts) < 4:
            continue
        dest, _mask, hop, iface = parts[0], parts[1], parts[2], parts[3]
        ours = (
            hop.startswith(TUN_ADDR_PREFIX)
            or iface.startswith(TUN_ADDR_PREFIX)
            or iface == token
            or row.endswith(f" {token}")
        )
        if dest == "0.0.0.0" and ours:
            lo = True
        if dest == "128.0.0.0" and ours:
            hi = True
    return lo and hi


def ensure_tun_split_default(if_idx: int, *, metric: int = 5) -> tuple[bool, str]:
    """Install /1+/1 on TUN; hop 172.19.0.1 first (same as the working build)."""
    from desktop.kill_switch import lift_ipv4_blackhole_commands

    run_route_lines(lift_ipv4_blackhole_commands())
    hops = tun_split_hops()
    last = ""
    for hop in hops:
        run_route_lines(install_tun_split_default(if_idx, metric=metric, hop=hop))
        if tun_split_installed(if_idx):
            ensure_lan_underlay()
            rows = " | ".join(tun_split_rows()[:4])
            return True, f"TUN split: {rows}"
        last = hop
    rows = " | ".join(tun_split_rows()[:4]) or "нет /1 в таблице"
    return False, f"TUN split не встал (последний hop={last}): {rows}"


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
        return underlay_gateway(dest)
    for raw in default_route_lines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        hop, iface = parts[2], parts[3]
        if iface in ips and hop.lower() not in {"on-link", "onlink", "0.0.0.0"}:
            return hop
    return underlay_gateway(dest)


def stale_default_cmds(keep_gw: str) -> list[str]:
    """Drop IPv4 defaults that are not the current underlay gateway.

    Office Ethernet / old TAP often leave 10.x defaults with metric 0.
    Tailscale/Amnezia leave 100.x (CGNAT) persistent rows with 'Default'
    instead of a metric — those used to be ignored and ate UDP to the VPS.
    """
    keep = (keep_gw or "").strip()
    cmds: list[str] = []
    seen: set[str] = set()
    if sys.platform != "win32" or not keep:
        return cmds
    for raw in route_print_v4().splitlines():
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
        out: list[str] = []
        for raw in route_print_v4().splitlines():
            if re.match(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+", raw):
                out.append(" ".join(raw.split()))
        return out[:8]
    try:
        r = procutil.run(["ip", "-4", "route", "show", "default"], timeout=3)
    except OSError:
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()][:8]


def _detect_bind_win(dest: str) -> str | None:
    """Windows NIC alias (e.g. Wi-Fi) used to reach dest before TUN is up."""
    ip = _resolve_host(dest)
    if not ip:
        return None
    idx = best_interface_index(ip)
    if not idx:
        return None
    name = _win_if_alias(idx)
    if name and not _is_tun_iface(name):
        return name
    return None


def _win_if_alias(if_index: int) -> str | None:
    for iface in netsh_ipv4_interfaces():
        if iface.idx == if_index:
            return iface.name or None
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
# hbbs / API / ws — VPS lets only loopback in. hbbr :21117 treats
# 127.0.0.1 as its admin console, so the relay stays on the WAN address.
RUSTDESK_HBBS_PORTS = [21114, 21115, 21116, 21118, 21119]
RUSTDESK_HBBR_PORTS = [21117]
RUSTDESK_PROCS = ["rustdesk.exe", "rustdesk_service.exe"]


class TunManager:
    """Locate/download sing-box and stop leftover TUN processes."""

    def __init__(self, var_dir: Path, tools_dir: Path, logs_dir: Path, log: LogFn = noop) -> None:
        self.var_dir = var_dir
        self.tools_dir = tools_dir
        self.log = log

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
                zip_member = next(
                    (
                        n
                        for n in zf.namelist()
                        if n.endswith("sing-box.exe") or n.endswith("ergoms-tun.exe")
                    ),
                    None,
                )
                if not zip_member:
                    raise RuntimeError("sing-box.exe not found in AWG zip")
                with zf.open(zip_member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                tar_member = next(
                    (
                        m
                        for m in tf.getmembers()
                        if m.name.endswith("/sing-box") or m.name == "sing-box"
                    ),
                    None,
                )
                if not tar_member:
                    raise RuntimeError("sing-box not found in AWG tar.gz")
                f = tf.extractfile(tar_member)
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


win_if_index_by_alias = _win_if_index_by_alias
win_if_alias = _win_if_alias
is_virtual_underlay = _is_virtual_underlay
direct_python_paths = _direct_python_paths
