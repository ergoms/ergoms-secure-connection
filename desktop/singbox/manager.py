"""sing-box process manager: config, start/stop, elevation, readiness."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from desktop import procutil
from desktop.branding import APP_EXE, APP_EXE_LEGACY
from desktop.config.constants import (
    AWG_DEFAULT_ADDRESS,
    AWG_DEFAULT_MTU,
    AWG_DEFAULT_PORT,
    REALITY_DEFAULT_SNI,
    TUN_DEFAULT_MTU,
    TUN_MTU_MAX,
    TUN_MTU_MIN,
)
from desktop.config.model import normalize_dial
from desktop.logutil import noop
from desktop.net_host import resolve_host
from desktop.proc_net import resolve_service_image
from desktop.route_tokens import parse_routes, process_matchers
from desktop.rustdesk_opt import rustdesk_tun_lan_reject_rules
from desktop.singbox.binaries import SingboxBinaries
from desktop.sys.constants import SINGBOX_PROCESS_NAMES
from desktop.tun import (
    RUSTDESK_HBBR_PORTS,
    RUSTDESK_HBBS_PORTS,
    detect_bind_interface,
    direct_python_paths,
    iface_ipv4s,
    remove_stale_tun_adapter,
    tun_iface_cidr,
    tun_iface_name,
)

_direct_python_paths = direct_python_paths
from lib.pac import bypass_to_singbox

LogFn = Callable[[str], None]


def parse_corporate_proxy(proxy: str) -> tuple[str, int]:
    raw = (proxy or "").replace("http://", "").replace("https://", "").strip("/")
    host, _, port_s = raw.partition(":")
    return host.strip(), int(port_s or "3128")


def amneziawg_opts(tr: dict[str, Any] | None) -> dict[str, Any] | None:
    """Home UDP AmneziaWG. Empty keys mean the protocol is off."""
    if not isinstance(tr, dict):
        return None
    raw = tr.get("amneziawg")
    if not isinstance(raw, dict):
        return None
    priv = str(raw.get("private_key") or "").strip()
    pub = str(raw.get("peer_public_key") or "").strip()
    if not priv or not pub or "REPLACE" in priv.upper() or "REPLACE" in pub.upper():
        return None
    address = str(raw.get("address") or AWG_DEFAULT_ADDRESS).strip() or AWG_DEFAULT_ADDRESS
    try:
        port = int(raw.get("port") or AWG_DEFAULT_PORT)
    except (TypeError, ValueError):
        port = AWG_DEFAULT_PORT
    try:
        mtu = int(raw.get("mtu") or AWG_DEFAULT_MTU)
    except (TypeError, ValueError):
        mtu = AWG_DEFAULT_MTU
    try:
        keepalive = int(raw.get("keepalive") or 25)
    except (TypeError, ValueError):
        keepalive = 25

    def _junk(name: str) -> int:
        try:
            return max(0, int(raw.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "private_key": priv,
        "peer_public_key": pub,
        "pre_shared_key": str(raw.get("pre_shared_key") or "").strip(),
        "address": address,
        "port": max(1, min(65535, port)),
        "mtu": max(1280, min(1500, mtu)),
        "jc": _junk("jc"),
        "jmin": _junk("jmin"),
        "jmax": _junk("jmax"),
        "s1": _junk("s1"),
        "s2": _junk("s2"),
        "h1": str(raw.get("h1") or "").strip(),
        "h2": str(raw.get("h2") or "").strip(),
        "h3": str(raw.get("h3") or "").strip(),
        "h4": str(raw.get("h4") or "").strip(),
        "keepalive": max(0, min(600, keepalive)),
    }


def awg_peer_address(server_host: str) -> str:
    """Prefer resolved VPS IPv4 so office DNS/dns-local cannot stall the handshake."""
    host = (server_host or "").strip()
    return resolve_host(host) or host


def awg_endpoint(
    server_host: str,
    opts: dict[str, Any],
    *,
    bind_iface: str | None = "",
    tag: str = "proxy",
) -> dict[str, Any]:
    addresses = [
        part.strip()
        for part in str(opts.get("address") or AWG_DEFAULT_ADDRESS).split(",")
        if part.strip()
    ]
    peer: dict[str, Any] = {
        "address": awg_peer_address(server_host),
        "port": int(opts["port"]),
        "public_key": opts["peer_public_key"],
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "persistent_keepalive_interval": int(opts.get("keepalive") or 25),
    }
    psk = str(opts.get("pre_shared_key") or "").strip()
    if psk:
        # AWG fork: peers.preshared_key (not WireGuard's pre_shared_key).
        peer["preshared_key"] = psk
    # spoofi/sing-box-awg 1.13 registers type "awg", not "wireguard".
    # Standard WG endpoint rejects jc/jmin/… as unknown fields.
    endpoint: dict[str, Any] = {
        "type": "awg",
        "tag": tag,
        "mtu": int(opts.get("mtu") or AWG_DEFAULT_MTU),
        "address": addresses or [AWG_DEFAULT_ADDRESS],
        "private_key": opts["private_key"],
        "peers": [peer],
    }
    for junk in ("jc", "jmin", "jmax", "s1", "s2"):
        junk_val = int(opts.get(junk) or 0)
        if junk_val:
            endpoint[junk] = junk_val
    for hdr in ("h1", "h2", "h3", "h4"):
        hdr_val = str(opts.get(hdr) or "").strip()
        if hdr_val:
            endpoint[hdr] = hdr_val
    endpoint.update(_udp_bind(bind_iface))
    # 1.13: peer hostname must resolve on underlay, not through the tunnel.
    endpoint["domain_resolver"] = "dns-local"
    return endpoint


def dial_label(dial: str) -> str:
    if dial == "amneziawg":
        return "AmneziaWG"
    return "VLESS+Reality"


def _dns_v12(
    *,
    docker_wsl_procs: list[str] | None = None,
    bypass_suffixes: list[str] | None = None,
    bypass_domains: list[str] | None = None,
) -> dict[str, Any]:
    """sing-box 1.12+ DNS (AWG fork). Official 1.11 still uses address: https://..."""
    rules: list[dict[str, Any]] = []
    if docker_wsl_procs:
        rules.append({"process_name": docker_wsl_procs, "server": "dns-local"})
    if bypass_suffixes:
        rules.append({"domain_suffix": bypass_suffixes, "server": "dns-local"})
    if bypass_domains:
        rules.append({"domain": bypass_domains, "server": "dns-local"})
    rules.append(
        {
            "domain_suffix": [".local", ".lan", ".internal", ".localhost"],
            "server": "dns-local",
        }
    )
    return {
        "servers": [
            {
                "type": "https",
                "tag": "dns-proxy",
                "server": "1.1.1.1",
                "path": "/dns-query",
                "detour": "proxy",
            },
            {
                "type": "local",
                "tag": "dns-local",
                "detour": "direct",
            },
        ],
        "rules": rules,
        "final": "dns-proxy",
        "strategy": "ipv4_only",
    }


def log_block(log_path: Path | str) -> dict[str, Any]:
    return {
        "level": "info",
        "timestamp": True,
        "output": str(log_path).replace("\\", "/"),
    }


def local_inbounds(socks_port: int, http_port: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "socks",
            "tag": "socks-in",
            "listen": "127.0.0.1",
            "listen_port": int(socks_port),
        },
        {
            "type": "http",
            "tag": "http-in",
            "listen": "127.0.0.1",
            "listen_port": int(http_port),
        },
    ]


def dns_block(
    *,
    schema: str = "v11",
    simple: bool = False,
    docker_wsl_procs: list[str] | None = None,
    bypass_suffixes: list[str] | None = None,
    bypass_domains: list[str] | None = None,
) -> dict[str, Any]:
    """sing-box 1.11 `address` DNS vs 1.12+ typed servers (AWG fork)."""
    if schema == "v12":
        return _dns_v12(
            docker_wsl_procs=docker_wsl_procs,
            bypass_suffixes=bypass_suffixes,
            bypass_domains=bypass_domains,
        )
    if simple:
        return {
            "servers": [
                {
                    "tag": "dns-proxy",
                    "address": "https://1.1.1.1/dns-query",
                    "detour": "proxy",
                }
            ],
            "final": "dns-proxy",
            "strategy": "ipv4_only",
        }
    return {
        "servers": [
            {
                "tag": "dns-proxy",
                "address": "https://1.1.1.1/dns-query",
                "detour": "proxy",
            },
            {
                "tag": "dns-proxy-dot",
                "address": "tls://1.1.1.1",
                "detour": "proxy",
            },
            {"tag": "dns-local", "address": "local", "detour": "direct"},
        ],
        "rules": [
            *(
                [{"process_name": docker_wsl_procs, "server": "dns-local"}]
                if docker_wsl_procs
                else []
            ),
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
        "strategy": "ipv4_only",
    }


def config_skeleton(
    *,
    log_path: Path | str,
    socks_port: int,
    http_port: int,
    dns_schema: str = "v11",
    dns: dict[str, Any] | None = None,
    simple_dns: bool = False,
) -> dict[str, Any]:
    return {
        "log": log_block(log_path),
        "dns": dns
        if dns is not None
        else dns_block(schema=dns_schema, simple=simple_dns),
        "inbounds": local_inbounds(socks_port, http_port),
    }


def _udp_bind(bind_iface: str | None) -> dict[str, Any]:
    """Windows: bind the NIC IPv4. bind_interface by name drops UDP."""
    if not bind_iface:
        return {}
    if sys.platform == "win32":
        ips = [
            ip
            for ip in iface_ipv4s(bind_iface)
            if not ip.startswith(("169.254.", "0."))
        ]
        if ips:
            return {"inet4_bind_address": ips[0]}
    return {"bind_interface": bind_iface}


def underlay_bind_target(
    server_host: str,
    exclude_ips: list[str],
    *,
    squid_host: str = "",
    udp_dial: bool = False,
) -> str:
    """NIC lookup dest. AWG must bind toward the VPS, not Squid."""
    if udp_dial:
        return awg_peer_address(server_host) or squid_host
    if exclude_ips:
        return exclude_ips[0]
    return squid_host or (server_host or "").strip()


def choose_dial(transport: dict[str, Any], *, office: bool) -> str:
    """Honor explicit dial. Empty: Reality in office, AmneziaWG at home."""
    raw = str((transport or {}).get("dial") or "").strip()
    if raw:
        return normalize_dial(raw)
    return "vless-reality" if office else "amneziawg"


def resolve_dial_bundle(
    transport: dict[str, Any], *, office: bool
) -> tuple[str, dict[str, Any] | None]:
    dial = choose_dial(transport, office=office)
    awg = amneziawg_opts(transport) if dial == "amneziawg" else None
    return dial, awg


def underlay_keep_hosts(
    server_host: str,
    corporate_proxy: str = "",
    *,
    udp_dial: bool = False,
) -> list[str]:
    """Hosts whose /32 must stay on the physical NIC (not TUN).

    Office VLESS goes out through Squid, so the VPS IP must *not* be pinned:
    otherwise ssh/RustDesk to the VPN host never enter TUN and die on the
    corporate firewall. AmneziaWG still needs the VPS /32 for its UDP handshake.
    """
    hosts: list[str] = []
    squid_host, _port = parse_corporate_proxy(corporate_proxy)
    if squid_host:
        hosts.append(squid_host)
    if udp_dial or not squid_host:
        host = (server_host or "").strip()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def underlay_forget_hosts(
    server_host: str,
    corporate_proxy: str = "",
    *,
    udp_dial: bool = False,
) -> list[str]:
    """VPS /32 that older builds pinned to Ethernet must be deleted in office mode.

    Squid is the only underlay pin. hbbs :21116 and SSH need the public VPS IP
    to follow TUN /1, then sing-box hairpin.
    """
    squid_host, _port = parse_corporate_proxy(corporate_proxy)
    if udp_dial or not squid_host:
        return []
    host = (server_host or "").strip()
    if not host or host == squid_host:
        return []
    return [host]


_PRIVATE_ROUTE_EXCLUDE = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "224.0.0.0/4",
]

_DOCKER_WSL_PROCS = [
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


def _route_process_names() -> list[str]:
    return [
        "sing-box",
        "sing-box.exe",
        "sing-box-awg",
        "ergoms-tun.exe",
        "ergoms-tun-awg.exe",
        f"{APP_EXE}.exe",
        *[f"{name}.exe" for name in APP_EXE_LEGACY],
    ]


def effective_tun_mtu(
    requested: int,
    *,
    awg_mtu: int | None = None,
    via_office_proxy: bool = False,
) -> int:
    """Inner TUN MTU under VLESS/AWG. 1500 fragments and makes SSH hitch."""
    try:
        want = int(requested)
    except (TypeError, ValueError):
        want = TUN_DEFAULT_MTU
    want = max(TUN_MTU_MIN, min(TUN_MTU_MAX, want))
    if awg_mtu:
        try:
            outer = max(TUN_MTU_MIN, int(awg_mtu))
        except (TypeError, ValueError):
            outer = AWG_DEFAULT_MTU
        return min(want, outer)
    if via_office_proxy:
        return min(want, TUN_MTU_MIN)
    return want


def _tun_inbound(mtu: int, *, kill_switch: bool, route_exclude: list[str]) -> dict[str, Any]:
    return {
        "type": "tun",
        "tag": "tun-in",
        "interface_name": tun_iface_name(),
        "address": [tun_iface_cidr()],
        "mtu": effective_tun_mtu(mtu),
        "auto_route": sys.platform != "win32",
        "strict_route": bool(kill_switch) and sys.platform != "win32",
        "stack": "mixed" if sys.platform == "win32" else "system",
        "route_exclude_address": route_exclude,
    }


def _vless_outbound(
    server_host: str,
    transport: dict[str, Any],
    *,
    bind_iface: str | None,
    use_office_proxy: bool,
    tag: str = "proxy",
    vision: bool = True,
) -> dict[str, Any]:
    outbound: dict[str, Any] = {
        "type": "vless",
        "tag": tag,
        "server": server_host,
        "server_port": int(transport["port"]),
        "uuid": transport["uuid"],
        "packet_encoding": "xudp",
        "tls": {
            "enabled": True,
            "server_name": str(transport["server_name"]),
            "utls": {"enabled": True, "fingerprint": "chrome"},
            "reality": {
                "enabled": True,
                "public_key": transport["public_key"],
                "short_id": transport["short_id"],
            },
        },
    }
    if vision:
        outbound["flow"] = "xtls-rprx-vision"
    if use_office_proxy:
        outbound["detour"] = "squid"
    elif bind_iface:
        outbound["bind_interface"] = bind_iface
    return outbound


def _ssh_port_match(ports: list[int]) -> int | list[int]:
    return ports if len(ports) > 1 else ports[0]


def _vps_loopback_rule(
    vps_ip: str,
    *,
    outbound: str = "proxy",
    port: int | list[int] | None = None,
    inbound: list[str] | None = None,
) -> dict[str, Any]:
    """Send traffic to the VPS public IP onto that host's loopback.

    The VPN endpoint *is* the VPS: connecting back to its WAN address from
    inside the tunnel is a hairpin (SSH timeout). RustDesk relay must not use
    this — see rustdesk_hairpin_rules.
    """
    rule: dict[str, Any] = {
        "ip_cidr": [f"{vps_ip}/32"],
        "outbound": outbound,
        "override_address": "127.0.0.1",
    }
    if port is not None:
        rule["port"] = port
    if inbound:
        rule["inbound"] = inbound
    return rule


def _host_is_ip(host: str) -> bool:
    text = (host or "").strip()
    if not text:
        return False
    try:
        from ipaddress import ip_address

        ip_address(text.split("%")[0])
        return True
    except ValueError:
        return False


def rustdesk_hairpin_rules(
    server_host: str, *, outbound: str = "proxy"
) -> list[dict[str, Any]]:
    """Reach RustDesk on the same VPS as the VPN (IP and hostname).

    hbbs (:21116) is only reachable via VPS loopback. hbbr (:21117) treats a
    127.0.0.1 peer as its admin console, so the relay keeps the WAN address
    (ens3 already holds it). These rules stay ahead of the generic VPS hairpin.
    """
    host = (server_host or "").strip()
    vps_ip = resolve_host(host) if host else ""
    rules: list[dict[str, Any]] = []

    def _add(ports: list[int], *, loopback: bool) -> None:
        extra = {"override_address": "127.0.0.1"} if loopback else {}
        if vps_ip:
            rules.append(
                {
                    "ip_cidr": [f"{vps_ip}/32"],
                    "port": ports,
                    "outbound": outbound,
                    **extra,
                }
            )
        if host and not _host_is_ip(host) and host != vps_ip:
            rules.append(
                {
                    "domain": [host],
                    "port": ports,
                    "outbound": outbound,
                    **extra,
                }
            )

    _add(RUSTDESK_HBBR_PORTS, loopback=False)
    _add(RUSTDESK_HBBS_PORTS, loopback=True)
    return rules


def _token_host_rules(parsed: Any, outbound: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if parsed.suffixes:
        rules.append({"domain_suffix": list(parsed.suffixes), "outbound": outbound})
    if parsed.domains:
        rules.append({"domain": list(parsed.domains), "outbound": outbound})
    if parsed.ips:
        rules.append({"ip_cidr": list(parsed.ips), "outbound": outbound})
    return rules


def _token_process_rules(parsed: Any, outbound: str) -> list[dict[str, Any]]:
    names: list[str] = []
    paths: list[str] = []
    seen_n: set[str] = set()
    seen_p: set[str] = set()
    for raw in parsed.processes:
        proc_names, proc_paths = process_matchers(raw)
        for name in proc_names:
            key = name.lower()
            if key in seen_n:
                continue
            seen_n.add(key)
            names.append(name)
        for path in proc_paths:
            key = path.lower()
            if key in seen_p:
                continue
            seen_p.add(key)
            paths.append(path)
    for svc in parsed.services:
        info = resolve_service_image(svc)
        if info is None or info.shared:
            continue
        if info.path:
            key = info.path.lower()
            if key not in seen_p:
                seen_p.add(key)
                paths.append(info.path)
        elif info.exe:
            proc_names, _ = process_matchers(info.exe)
            for name in proc_names:
                key = name.lower()
                if key in seen_n:
                    continue
                seen_n.add(key)
                names.append(name)
    rules: list[dict[str, Any]] = []
    if names:
        rules.append({"process_name": names, "outbound": outbound})
    if paths:
        rules.append({"process_path": paths, "outbound": outbound})
    return rules


def _build_route_rules(
    *,
    server_host: str,
    exclude_ips: list[str],
    vpn_port: int,
    vps_proxy_ports: list[int] | None,
    bypass_hosts: list[str],
    vpn_hosts: list[str] | None = None,
    via_proxy: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rules: list[dict[str, Any]] = []
    vps_ip = resolve_host(server_host)
    ssh_ports = [int(p) for p in (vps_proxy_ports or [22]) if 1 <= int(p) <= 65535]
    if vps_ip:
        rules.append({"ip_cidr": [f"{vps_ip}/32"], "port": vpn_port, "outbound": "direct"})
        if via_proxy:
            # Office: VPS /32 stays on TUN. Services on that host go through
            # VLESS to 127.0.0.1. RustDesk ports are added before sniff.
            rules.append(_vps_loopback_rule(vps_ip))
        else:
            if ssh_ports:
                # Reverse SSH = SOCKS ProxyCommand → VLESS.
                rules.append(
                    _vps_loopback_rule(
                        vps_ip,
                        port=_ssh_port_match(ssh_ports),
                        inbound=["socks-in", "http-in"],
                    )
                )
    if exclude_ips:
        rules.append({"ip_cidr": [f"{ip}/32" for ip in exclude_ips], "outbound": "direct"})
    vpn_parsed = parse_routes(vpn_hosts or [])
    rules.extend(_token_host_rules(vpn_parsed, "proxy"))
    rules.extend(_token_process_rules(vpn_parsed, "proxy"))
    direct_parsed = parse_routes(bypass_hosts)
    bypass_suffixes, bypass_domains = bypass_to_singbox(bypass_hosts)
    rules.extend(_token_host_rules(direct_parsed, "direct"))
    rules.extend(_token_process_rules(direct_parsed, "direct"))
    # Office DNS (10.16.0.9) is private. Hijack-before-private sent every
    # Chrome lookup through VLESS→DoH (~200ms) and YouTube crawled.
    rules.append({"ip_is_private": True, "outbound": "direct"})
    rules.append({"port": 53, "action": "hijack-dns"})
    if via_proxy:
        # Chrome HTTP/3 over VLESS→Squid CONNECT stalls for seconds, then
        # falls back to TCP. Fail QUIC immediately so the tab does not lag.
        rules.append({"network": "udp", "port": 443, "action": "reject"})
    rules.append({"inbound": ["socks-in", "http-in"], "outbound": "proxy"})
    rules.append({"process_name": _DOCKER_WSL_PROCS, "outbound": "direct"})
    rules.append({"process_name": _route_process_names(), "outbound": "direct"})
    py_paths = _direct_python_paths()
    if py_paths:
        rules.append({"process_path": py_paths, "outbound": "direct"})
    return rules, bypass_suffixes, bypass_domains


def require_transport(cfg: dict[str, Any]) -> dict[str, Any]:
    tr = cfg.get("transport")
    if not isinstance(tr, dict):
        raise RuntimeError("Нет настроек подключения. Загрузите конфиг.")
    uuid = str(tr.get("uuid") or "").strip()
    pub = str(tr.get("public_key") or "").strip()
    short_id = str(tr.get("short_id") or "").strip()
    sni = str(tr.get("server_name") or REALITY_DEFAULT_SNI).strip() or REALITY_DEFAULT_SNI
    typ = str(tr.get("type") or "vless-reality").strip().lower()
    if typ not in ("vless-reality", "vless", "reality"):
        raise RuntimeError("Неизвестный тип подключения. Загрузите конфиг.")
    port = int(tr.get("port") or 443)
    dial = normalize_dial(tr.get("dial"))
    awg = amneziawg_opts(tr)
    out = {
        "uuid": uuid,
        "public_key": pub,
        "short_id": short_id,
        "server_name": sni,
        "port": port,
        "type": "vless-reality",
        "dial": dial,
    }
    if awg:
        out["amneziawg"] = awg
    if dial == "amneziawg":
        if not awg:
            raise RuntimeError("AmneziaWG: загрузите .conf в Настройках")
        return out
    if not uuid or "REPLACE" in uuid.upper() or len(uuid) < 8:
        raise RuntimeError("Загрузите конфиг Reality")
    if not pub or "REPLACE" in pub.upper():
        raise RuntimeError("Загрузите конфиг Reality")
    return out


class SingboxModeManager:
    """Run sing-box with VLESS via corporate HTTP CONNECT (+ local mixed/TUN)."""

    def __init__(
        self,
        var_dir: Path,
        tools_dir: Path,
        logs_dir: Path,
        log: LogFn = noop,
    ) -> None:
        self.var_dir = var_dir
        self.tools_dir = tools_dir
        self.logs_dir = logs_dir
        self.log = log
        self.config_path = var_dir / "sing-box-mode.json"
        self.pid_path = var_dir / "sing-box-mode.pid"
        self.log_path = logs_dir / "sing-box-mode.log"
        self._tun_helper = SingboxBinaries(var_dir, tools_dir, logs_dir, log=log)
        self._pid_scan_at = 0.0
        self._pid_scan_result: int | None = None

    def find_sing_box(self, explicit: str = "") -> Path | None:
        return self._tun_helper.find_sing_box(explicit)

    def find_awg_sing_box(self) -> Path | None:
        return self._tun_helper.find_awg_sing_box()

    def awg_version_ok(self) -> bool:
        return self._tun_helper.awg_version_ok()

    def ensure_downloaded(self, proxy_url: str | None = None) -> Path:
        return self._tun_helper.ensure_downloaded(proxy_url=proxy_url)

    def ensure_awg_downloaded(self, proxy_url: str | None = None) -> Path:
        return self._tun_helper.ensure_awg_downloaded(proxy_url=proxy_url)

    def build_config(
        self,
        *,
        server_host: str,
        transport: dict[str, Any],
        corporate_proxy: str,
        socks_port: int,
        http_port: int,
        enable_tun: bool,
        bypass_hosts: list[str] | None = None,
        vpn_hosts: list[str] | None = None,
        mtu: int = 1400,
        vps_proxy_ports: list[int] | None = None,
        kill_switch: bool = False,
        rustdesk: bool | None = None,
    ) -> dict[str, Any]:
        if rustdesk is None:
            from desktop.config_io import get_rustdesk_enabled

            rustdesk = get_rustdesk_enabled()
        squid_host, squid_port = parse_corporate_proxy(corporate_proxy)
        use_office_proxy = bool(squid_host)
        office = bool(squid_host)
        dial, awg = resolve_dial_bundle(transport, office=office)
        exclude_ips: list[str] = []
        for h in underlay_keep_hosts(
            server_host, corporate_proxy, udp_dial=bool(awg)
        ):
            ip = resolve_host(h)
            if ip:
                exclude_ips.append(ip)
        bind_target = underlay_bind_target(
            server_host,
            exclude_ips,
            squid_host=squid_host,
            udp_dial=bool(awg),
        )
        bind_iface = detect_bind_interface(bind_target) if bind_target else ""
        if bind_iface:
            self.log(
                f"underlay NIC: {bind_iface} — outbound и direct сидят на ней, "
                f"auto_detect выкл, exclude={', '.join(exclude_ips) or 'нет'}"
            )
        elif enable_tun:
            self.log("underlay NIC: не определён — outbound может уйти в TUN")
        if awg and not enable_tun:
            place = "офис" if office else "дом"
            extra = " (минуя Squid)" if office else ""
            self.log(f"{place}: AmneziaWG UDP :{awg['port']}{extra}")
            return self._minimal_awg_config(
                server_host=server_host,
                awg=awg,
                bind_iface=bind_iface,
                socks_port=socks_port,
                http_port=http_port,
            )

        route_exclude = list(_PRIVATE_ROUTE_EXCLUDE)
        for ip in exclude_ips:
            cidr = f"{ip}/32"
            if cidr not in route_exclude:
                route_exclude.append(cidr)
        vpn_port = int(
            (awg or {}).get("port")
            or transport.get("port")
            or 443
        )
        place = "офис" if office else "дом"
        if dial == "amneziawg" and awg:
            extra = " (минуя Squid)" if office else ""
            self.log(f"{place}: AmneziaWG UDP :{awg['port']}{extra}")
        elif office:
            self.log("офис: VLESS+Reality через Squid")
            if enable_tun and not awg:
                self.log(
                    "офис: SSH на IP VPS идёт через VLESS → 127.0.0.1 "
                    "(иначе hairpin на публичный адрес)"
                )
                if rustdesk:
                    self.log(
                        "офис: RustDesk hbbs → 127.0.0.1, релей :21117 на WAN"
                    )
        rules, bypass_suffixes, bypass_domains = _build_route_rules(
            server_host=server_host,
            exclude_ips=exclude_ips,
            vpn_port=vpn_port,
            vps_proxy_ports=vps_proxy_ports,
            bypass_hosts=bypass_hosts or [],
            vpn_hosts=vpn_hosts or [],
            via_proxy=use_office_proxy and not awg,
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        inbounds: list[dict[str, Any]] = local_inbounds(socks_port, http_port)
        tun_mtu = effective_tun_mtu(
            mtu,
            awg_mtu=int(awg["mtu"]) if awg else None,
            via_office_proxy=use_office_proxy and not awg,
        )
        if enable_tun and tun_mtu != mtu:
            self.log(f"TUN MTU {tun_mtu} (в запросе {mtu} — иначе SSH/TCP режутся на фрагменты)")
        if enable_tun:
            inbounds.append(
                _tun_inbound(tun_mtu, kill_switch=kill_switch, route_exclude=route_exclude)
            )
        bind = {"bind_interface": bind_iface} if bind_iface else {}
        outbounds: list[dict[str, Any]] = []
        if awg:
            outbounds.append({"type": "direct", "tag": "direct", **bind})
        else:
            if use_office_proxy:
                outbounds.append(
                    {
                        "type": "http",
                        "tag": "squid",
                        "server": squid_host,
                        "server_port": int(squid_port),
                        **bind,
                    }
                )
            outbounds.append(
                _vless_outbound(
                    server_host,
                    transport,
                    bind_iface=bind_iface,
                    use_office_proxy=use_office_proxy,
                )
            )
            outbounds.append({"type": "direct", "tag": "direct", **bind})
            outbounds.append({"type": "block", "tag": "block"})
        sniff = [
            {"inbound": ["socks-in", "http-in"], "action": "sniff", "timeout": "100ms"}
        ]
        if enable_tun:
            sniff.append(
                {
                    "inbound": ["tun-in"],
                    "network": "tcp",
                    "action": "sniff",
                    "timeout": "100ms",
                }
            )
        rustdesk_rules = (
            [
                *rustdesk_hairpin_rules(server_host),
                *rustdesk_tun_lan_reject_rules(),
            ]
            if rustdesk
            else []
        )
        box: dict[str, Any] = {
            **config_skeleton(
                log_path=self.log_path,
                socks_port=socks_port,
                http_port=http_port,
                dns=dns_block(
                    schema="v11",
                    docker_wsl_procs=_DOCKER_WSL_PROCS,
                    bypass_suffixes=bypass_suffixes,
                    bypass_domains=bypass_domains,
                ),
            ),
            "inbounds": inbounds,
            "outbounds": outbounds,
            "route": {
                "auto_detect_interface": not bool(bind_iface),
                **({"default_interface": bind_iface} if bind_iface else {}),
                "final": "proxy",
                "rules": [*rustdesk_rules, *sniff, *rules],
            },
        }
        if awg:
            box["dns"] = dns_block(
                schema="v12",
                docker_wsl_procs=_DOCKER_WSL_PROCS,
                bypass_suffixes=bypass_suffixes,
                bypass_domains=bypass_domains,
            )
            box["endpoints"] = [awg_endpoint(server_host, awg, bind_iface=bind_iface)]
            box["route"]["default_domain_resolver"] = "dns-local"
        return box

    def _minimal_awg_config(
        self,
        *,
        server_host: str,
        awg: dict[str, Any],
        bind_iface: str | None,
        socks_port: int,
        http_port: int,
    ) -> dict[str, Any]:
        """SOCKS/HTTP only — TUN after handshake."""
        self.log("AmneziaWG как в песочнице — без TUN на handshake")
        bind = _udp_bind(bind_iface)
        ip = str(bind.get("inet4_bind_address") or "")
        if ip:
            self.log(f"AWG UDP с {ip}")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        return {
            **config_skeleton(
                log_path=self.log_path,
                socks_port=socks_port,
                http_port=http_port,
                dns_schema="v12",
            ),
            "endpoints": [
                awg_endpoint(server_host, awg, bind_iface=bind_iface)
            ],
            "outbounds": [
                {
                    "type": "direct",
                    "tag": "direct",
                    **({"bind_interface": bind_iface} if bind_iface else {}),
                }
            ],
            "route": {
                "auto_detect_interface": False,
                **({"default_interface": bind_iface} if bind_iface else {}),
                "default_domain_resolver": "dns-local",
                "final": "proxy",
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
        found = self._find_pid()
        if found and not procutil.is_sing_box_pid(found):
            found = None
        self._pid_scan_at = now
        self._pid_scan_result = found
        if found:
            try:
                self.pid_path.write_text(str(found), encoding="utf-8")
            except OSError:
                # Root-owned pid after `sudo on` — status as user must still work.
                pass
            return found
        return None

    def tun_active(self) -> bool:
        """True if the sing-box process is up (TUN lives in the same process)."""
        return self.running()

    def start(
        self,
        *,
        server_host: str,
        transport: dict[str, Any],
        corporate_proxy: str,
        socks_port: int,
        http_port: int,
        enable_tun: bool,
        sing_box_path: str = "",
        elevate: bool = True,
        bypass_hosts: list[str] | None = None,
        vpn_hosts: list[str] | None = None,
        mtu: int = 1400,
        vps_proxy_ports: list[int] | None = None,
        force_restart: bool = False,
        kill_switch: bool = False,
        rustdesk: bool | None = None,
        prelude_cmds: list[str] | None = None,
        postlude_cmds: list[str] | None = None,
    ) -> None:
        exe = self.find_sing_box(sing_box_path)
        if not exe:
            raise RuntimeError(
                "sing-box не найден. Выполните: ergoms-secure-connection download-sing-box"
            )

        cfg = self.build_config(
            server_host=server_host,
            transport=transport,
            corporate_proxy=corporate_proxy,
            socks_port=socks_port,
            http_port=http_port,
            enable_tun=enable_tun,
            bypass_hosts=bypass_hosts,
            vpn_hosts=vpn_hosts,
            mtu=mtu,
            vps_proxy_ports=vps_proxy_ports,
            kill_switch=kill_switch,
            rustdesk=rustdesk,
        )
        config_text = json.dumps(cfg, indent=2)
        if self.running():
            if not force_restart:
                old = (
                    self.config_path.read_text(encoding="utf-8")
                    if self.config_path.is_file()
                    else ""
                )
                if old == config_text:
                    self.log(f"singbox mode already running (pid={self.pid()})")
                    return
            self.log("singbox mode config changed — restart")
            self.stop()

        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(config_text, encoding="utf-8")

        need_admin = bool(enable_tun and elevate)
        office = bool((corporate_proxy or "").strip())
        dial = choose_dial(transport, office=office)
        awg = amneziawg_opts(transport) if dial == "amneziawg" else None
        if awg:
            dest = f"AmneziaWG {server_host}:{awg['port']}/udp"
        else:
            dest = f"VLESS {server_host}:{transport['port']}"
        self.log(
            f"Starting MODE=singbox → {dest}"
            f"{' via proxy' if office and dial == 'vless-reality' else ''}"
            f"; socks=:{socks_port} http=:{http_port} tun={int(enable_tun)}"
            f" kill_switch={int(kill_switch)}"
        )
        if need_admin and not procutil.is_admin():
            self.log("TUN: нужен один запрос прав — дальше без окон Windows")
        self._ensure_win_firewall(exe)

        stale = procutil.tun_bin_pids()
        if stale:
            self.log(f"старый tun ещё жив pid={','.join(str(p) for p in stale)} — останавливаю")
            self.stop()
        if enable_tun:
            remove_stale_tun_adapter(log=self.log, hidden=True)

        self._rotate_log()
        fw = self._firewall_cmds(exe) if need_admin and not procutil.is_admin() else []
        prelude = [*(prelude_cmds or []), *fw]
        postlude = [c for c in (postlude_cmds or []) if c]
        pid = self._launch(
            exe, elevate=need_admin, prelude_cmds=prelude, postlude_cmds=postlude
        )
        if pid:
            self.pid_path.write_text(str(pid), encoding="utf-8")
            self.log(f"sing-box pid={pid}")
        else:
            self.log("ожидаю появления процесса sing-box…")

        if self._wait_ready(
            socks_port, http_port, enable_tun=enable_tun, pid=pid
        ):
            return
        if enable_tun:
            reason = "занят" if self._tun_adapter_busy() else "не появился"
            self.log(f"TUN {reason} — останавливаю и пробую ещё раз")
            leftover = self.pid() or pid
            if leftover and procutil.pid_alive(leftover):
                procutil.kill_pids([leftover])
            remove_stale_tun_adapter(log=self.log, hidden=True)
            # Same log file still has the previous FATAL "already exists" —
            # wait_ready would abort the retry before the new process writes.
            self._rotate_log()
            time.sleep(1.0)
            pid = self._launch(
                exe, elevate=need_admin, prelude_cmds=prelude, postlude_cmds=postlude
            )
            if pid:
                self.pid_path.write_text(str(pid), encoding="utf-8")
                self.log(f"sing-box pid={pid} (повтор)")
            if self._wait_ready(
                socks_port,
                http_port,
                enable_tun=enable_tun,
                pid=pid,
                timeout=22.0,
            ):
                return

        self._log_tail("не поднялся SOCKS" if not enable_tun else "не поднялся TUN")
        raise RuntimeError(
            f"sing-box не открыл SOCKS :{socks_port}"
            + (" или TUN" if enable_tun else "")
            + f". См. {self.log_path}. Нужен download-sing-box и верные transport.*"
        )

    def tail_log(self, n: int = 20) -> list[str]:
        if not self.log_path.is_file():
            return []
        try:
            size = self.log_path.stat().st_size
            with self.log_path.open("rb") as fh:
                # Keep enough prefix to see leftover FATAL under SOCKS flood.
                if size > 262144:
                    fh.seek(size - 262144)
                raw = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = raw.splitlines()
        return [ln.rstrip() for ln in lines[-n:] if ln.strip()]

    def _rotate_log(self) -> None:
        """New session = empty log, so the UI does not dump the previous run."""
        path = self.log_path
        try:
            if path.is_file() and path.stat().st_size > 0:
                backup = path.with_name(path.name + ".1")
                backup.unlink(missing_ok=True)
                path.replace(backup)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        except OSError:
            pass

    def _tun_adapter_busy(self) -> bool:
        from desktop.singbox.readiness import TUN_LOG_SCAN_LINES, tun_adapter_busy

        return tun_adapter_busy(self.tail_log(TUN_LOG_SCAN_LINES))

    def _tun_log_started(self) -> bool:
        from desktop.singbox.readiness import tun_log_started

        return tun_log_started(self.tail_log(60))

    def _tun_still_opening(self) -> bool:
        from desktop.singbox.readiness import tun_still_opening

        return tun_still_opening(self.tail_log(60))

    def _tun_inbound_ready(self) -> bool:
        from desktop.singbox.readiness import tun_inbound_ready

        return tun_inbound_ready(self.tail_log(60))

    def _wait_ready(
        self,
        socks_port: int,
        http_port: int,
        *,
        enable_tun: bool,
        pid: int | None,
        timeout: float = 22.0,
    ) -> bool:
        from desktop.singbox.readiness import wait_ready

        return wait_ready(
            socks_port=socks_port,
            http_port=http_port,
            enable_tun=enable_tun,
            pid=pid,
            log=self.log,
            tail=self.tail_log,
            live_pid=self.pid,
            timeout=timeout,
        )

    def _log_tail(self, reason: str, n: int = 20) -> None:
        lines = self.tail_log(n)
        if not lines:
            self.log(f"журнал sing-box пуст ({self.log_path.name})")
            return
        self.log(f"журнал sing-box — {reason}:")
        for ln in lines:
            self.log(f"  {ln}")

    def stop(self) -> None:
        procutil.invalidate_proc_cache()
        self._pid_scan_at = 0.0
        self._pid_scan_result = None
        targets: list[int] = []
        pid = self.pid()
        if pid:
            targets.append(pid)
        for orphan in procutil.pids_named(*SINGBOX_PROCESS_NAMES):
            if orphan not in targets:
                targets.append(orphan)
        if not targets:
            self.log("sing-box уже не запущен")
            self.pid_path.unlink(missing_ok=True)
            remove_stale_tun_adapter(log=self.log, hidden=True)
            return

        self.log(f"остановка sing-box pid={','.join(str(p) for p in targets)}")
        died = procutil.kill_pids(targets)
        leftover = procutil.tun_bin_pids()
        if leftover:
            if procutil.is_admin():
                self.log("добиваю ergoms-tun / sing-box от администратора")
            else:
                self.log("процесс с повышенными правами — один запрос UAC на остановку")
            if not procutil.kill_tun_binaries(elevate_if_needed=True):
                leftover = procutil.tun_bin_pids()
                self.pid_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Не удалось остановить tun pid={','.join(str(p) for p in leftover)}"
                )
            leftover = procutil.tun_bin_pids()
            if leftover:
                self.pid_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"tun pid={','.join(str(p) for p in leftover)} всё ещё работает"
                )
        self.log(f"sing-box остановлен (pid={','.join(str(p) for p in died or targets)})")
        self._pid_scan_at = 0.0
        self._pid_scan_result = None
        self.pid_path.unlink(missing_ok=True)
        remove_stale_tun_adapter(log=self.log, hidden=True)

    def _launch(
        self,
        exe: Path,
        *,
        elevate: bool,
        prelude_cmds: list[str] | None = None,
        postlude_cmds: list[str] | None = None,
    ) -> int | None:
        from desktop.singbox.spawn import (
            LinuxElevatedSpawn,
            NormalSpawn,
            WinElevatedSpawn,
            needs_linux_elevation,
            needs_win_elevation,
        )

        args = [str(exe), "run", "-c", str(self.config_path)]
        prelude = [c for c in (prelude_cmds or []) if c]
        postlude = [c for c in (postlude_cmds or []) if c]
        if needs_win_elevation(elevate=elevate):
            return WinElevatedSpawn().start(
                exe,
                self.config_path,
                self.var_dir,
                prelude,
                postlude,
                self._find_pid,
            )
        if needs_linux_elevation(elevate=elevate):
            return LinuxElevatedSpawn().start(
                args, self.log_path, prelude, self._find_pid, self.log
            )
        return NormalSpawn().start(args, self.log_path, postlude, self._run_postlude)

    def _run_postlude(self, cmds: list[str]) -> None:
        """Re-pin underlay /32 after TUN auto_route (already-admin path)."""
        for wait in (0.2, 0.6, 1.2, 2.5):
            time.sleep(wait)
            for line in cmds:
                args = [p for p in line.split(" ") if p]
                procutil.run(args, timeout=8)

    def _start_elevated_win(
        self,
        exe: Path,
        config: Path,
        prelude: list[str] | None = None,
        postlude: list[str] | None = None,
    ) -> int | None:
        import ctypes

        pre = [c for c in (prelude or []) if c]
        post = [c for c in (postlude or []) if c]
        if pre or post:
            wrapper = self.var_dir / "sing-box-elevated.cmd"
            lines = ["@echo off"]
            for cmd in pre:
                lines.append(f"{cmd} 2>nul")
            if post:
                delayed = "timeout /t 2 /nobreak >nul"
                for cmd in post:
                    delayed += f" & {cmd} 2>nul"
                lines.append(f'start "ergoms-pin" /b cmd /c "{delayed}"')
            lines.append(f'"{exe}" run -c "{config}"')
            wrapper.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
            file, params, cwd = str(wrapper), "", str(self.var_dir)
        else:
            file, params, cwd = str(exe), f'run -c "{config}"', str(exe.parent)
        rc = int(
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", file, params, cwd, 0
            )
        )
        if rc <= 32:
            raise RuntimeError(
                f"UAC для sing-box mode не удался (код {rc}). "
                "Запустите от администратора или TUN=0."
            )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            found = self._find_pid()
            if found:
                return found
            time.sleep(0.05)
        return None

    def _start_elevated_linux(
        self, args: list[str], prelude: list[str] | None = None
    ) -> int | None:
        import shlex

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        if prelude:
            inner = " ; ".join(prelude) + " ; exec " + " ".join(
                shlex.quote(a) for a in args
            )
            launched = ["sh", "-c", inner]
        else:
            launched = args
        for wrapper in (
            ["pkexec", *launched],
            ["sudo", "-n", *launched],
            ["sudo", *launched],
        ):
            try:
                proc = subprocess.Popen(
                    wrapper,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(0.5)
                if proc.poll() is None or self._find_pid():
                    self.log(f"singbox mode via {wrapper[0]}")
                    return proc.pid if proc.poll() is None else self._find_pid()
            except FileNotFoundError:
                continue
        raise RuntimeError(
            "Не удалось запустить sing-box mode с root (pkexec/sudo). "
            "Или TUN=0, или: sudo python -m desktop on"
        )

    def _find_pid(self) -> int | None:
        for pid in procutil.pids_named(*SINGBOX_PROCESS_NAMES):
            return pid
        return None

    def _firewall_cmds(self, exe: Path) -> list[str]:
        name = "ERGOMS SECURE CONNECTION (sing-box)"
        return [
            (
                "netsh advfirewall firewall add rule "
                f'name="{name}" dir={direction} action=allow '
                f'program="{exe}" enable=yes profile=any'
            )
            for direction in ("in", "out")
        ]

    def _ensure_win_firewall(self, exe: Path) -> None:
        """Allow sing-box once so Windows does not show the firewall popup."""
        if sys.platform != "win32" or not procutil.is_admin():
            return
        marker = self.var_dir / "firewall-sing-box.flag"
        key = str(exe.resolve()).lower()
        try:
            if marker.is_file() and marker.read_text(encoding="utf-8").strip() == key:
                return
        except OSError:
            pass
        name = "ERGOMS SECURE CONNECTION (sing-box)"
        for direction in ("in", "out"):
            procutil.run(
                [
                    "netsh",
                    "advfirewall",
                    "firewall",
                    "add",
                    "rule",
                    f"name={name}",
                    f"dir={direction}",
                    "action=allow",
                    f"program={exe}",
                    "enable=yes",
                    "profile=any",
                ],
                timeout=8,
            )
        try:
            marker.write_text(key, encoding="utf-8")
        except OSError:
            pass
        self.log("брандмауэр: sing-box разрешён без окна Windows")


