"""MODE=singbox: one sing-box process (VLESS+Reality via Squid + mixed + optional TUN)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from desktop import procutil
from desktop.branding import APP_EXE, APP_EXE_LEGACY
from desktop.config_io import (
    AWG_DEFAULT_ADDRESS,
    AWG_DEFAULT_MTU,
    AWG_DEFAULT_PORT,
    HY2_DEFAULT_SNI,
    REALITY_DEFAULT_SNI,
    normalize_dial,
    normalize_hy2_sni,
)
from desktop.tun import (
    RUSTDESK_PORTS,
    TUN_IFACE_NAME,
    TunManager,
    _direct_python_paths,
    _resolve_host,
    detect_bind_interface,
    iface_ipv4s,
)
from lib.http_via_socks import bypass_to_singbox

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def parse_corporate_proxy(proxy: str) -> tuple[str, int]:
    raw = (proxy or "").replace("http://", "").replace("https://", "").strip("/")
    host, _, port_s = raw.partition(":")
    return host.strip(), int(port_s or "3128")


def hysteria2_opts(tr: dict[str, Any] | None) -> dict[str, Any] | None:
    """Home UDP transport. Empty/missing password means Hysteria2 is off."""
    if not isinstance(tr, dict):
        return None
    raw = tr.get("hysteria2")
    if not isinstance(raw, dict):
        password = str(tr.get("hy2_password") or "").strip()
        raw = {"password": password} if password else None
    if not isinstance(raw, dict):
        return None
    password = str(raw.get("password") or "").strip()
    if not password or "REPLACE" in password.upper():
        return None
    sni = normalize_hy2_sni(raw.get("server_name"), fallback=HY2_DEFAULT_SNI)
    obfs = str(raw.get("obfs_password") or "").strip()
    blob = raw.get("obfs")
    if isinstance(blob, dict):
        obfs = str(blob.get("password") or obfs).strip()
    elif isinstance(blob, str) and blob.strip():
        obfs = blob.strip()
    return {
        "password": password,
        "port": int(raw.get("port") or 8443),
        "server_name": sni,
        "insecure": bool(raw.get("insecure", True)),
        "obfs_password": obfs,
    }


def hy2_outbound(
    server_host: str,
    hy: dict[str, Any],
    *,
    bind_iface: str = "",
    tag: str = "proxy",
) -> dict[str, Any]:
    outbound: dict[str, Any] = {
        "type": "hysteria2",
        "tag": tag,
        "server": server_host,
        "server_port": int(hy["port"]),
        "password": hy["password"],
        # Home UDP is lossy (DPI / Amnezia WFP). Default QUIC handshake is 5s.
        "connect_timeout": "15s",
        "tls": {
            "enabled": True,
            "server_name": hy["server_name"],
            "insecure": bool(hy.get("insecure", True)),
            "alpn": ["h3"],
        },
    }
    obfs = str(hy.get("obfs_password") or "").strip()
    if obfs:
        outbound["obfs"] = {"type": "salamander", "password": obfs}
    outbound.update(_udp_bind(bind_iface))
    return outbound


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


def awg_endpoint(
    server_host: str,
    opts: dict[str, Any],
    *,
    bind_iface: str = "",
    tag: str = "proxy",
) -> dict[str, Any]:
    addresses = [
        part.strip()
        for part in str(opts.get("address") or AWG_DEFAULT_ADDRESS).split(",")
        if part.strip()
    ]
    peer: dict[str, Any] = {
        "address": server_host,
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
        val = int(opts.get(junk) or 0)
        if val:
            endpoint[junk] = val
    for hdr in ("h1", "h2", "h3", "h4"):
        val = str(opts.get(hdr) or "").strip()
        if val:
            endpoint[hdr] = val
    endpoint.update(_udp_bind(bind_iface))
    # 1.13: peer hostname must resolve on underlay, not through the tunnel.
    endpoint["domain_resolver"] = "dns-local"
    return endpoint


def dial_label(dial: str) -> str:
    if dial == "hysteria2":
        return "Hysteria2"
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
        "strategy": "prefer_ipv4",
    }


def _udp_bind(bind_iface: str) -> dict[str, Any]:
    """Windows: bind the NIC IPv4. bind_interface by name drops Hy2 UDP."""
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


def choose_dial(transport: dict[str, Any], *, office: bool) -> str:
    """Office always Reality (Squid has no UDP). Home: explicit dial."""
    if office:
        return "vless-reality"
    return normalize_dial((transport or {}).get("dial"))


def require_transport(cfg: dict[str, Any]) -> dict[str, Any]:
    tr = cfg.get("transport")
    if not isinstance(tr, dict):
        raise RuntimeError(
            "MODE=singbox requires config.json transport{} "
            "(uuid, public_key, short_id, server_name) — run VPS bootstrap_singbox_443.sh"
        )
    uuid = str(tr.get("uuid") or "").strip()
    pub = str(tr.get("public_key") or "").strip()
    short_id = str(tr.get("short_id") or "").strip()
    sni = str(tr.get("server_name") or REALITY_DEFAULT_SNI).strip() or REALITY_DEFAULT_SNI
    typ = str(tr.get("type") or "vless-reality").strip().lower()
    if typ not in ("vless-reality", "vless", "reality", "hysteria2"):
        raise RuntimeError(f"Unsupported transport.type={typ} (use vless-reality)")
    if not uuid or "REPLACE" in uuid.upper() or len(uuid) < 8:
        raise RuntimeError("transport.uuid missing — paste from VPS bootstrap output")
    if not pub or "REPLACE" in pub.upper():
        raise RuntimeError("transport.public_key missing — paste from VPS bootstrap")
    port = int(tr.get("port") or 443)
    dial = normalize_dial(tr.get("dial"))
    out = {
        "uuid": uuid,
        "public_key": pub,
        "short_id": short_id,
        "server_name": sni,
        "port": port,
        "type": "vless-reality",
        "dial": dial,
    }
    hy = hysteria2_opts(tr)
    if hy:
        out["hysteria2"] = hy
    awg = amneziawg_opts(tr)
    if awg:
        out["amneziawg"] = awg
    return out


class SingboxModeManager:
    """Run sing-box with VLESS via corporate HTTP CONNECT (+ local mixed/TUN)."""

    def __init__(
        self,
        var_dir: Path,
        tools_dir: Path,
        logs_dir: Path,
        log: LogFn = _noop,
    ) -> None:
        self.var_dir = var_dir
        self.tools_dir = tools_dir
        self.logs_dir = logs_dir
        self.log = log
        self.config_path = var_dir / "sing-box-mode.json"
        self.pid_path = var_dir / "sing-box-mode.pid"
        self.log_path = logs_dir / "sing-box-mode.log"
        self._tun_helper = TunManager(var_dir, tools_dir, logs_dir, log=log)
        self._pid_scan_at = 0.0
        self._pid_scan_result: int | None = None

    def find_sing_box(self, explicit: str = "") -> Path | None:
        return self._tun_helper.find_sing_box(explicit)

    def find_awg_sing_box(self) -> Path | None:
        return self._tun_helper.find_awg_sing_box()

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
        mtu: int = 1400,
        vps_proxy_ports: list[int] | None = None,
        kill_switch: bool = False,
    ) -> dict[str, Any]:
        squid_host, squid_port = parse_corporate_proxy(corporate_proxy)
        use_office_proxy = bool(squid_host)

        exclude_ips: list[str] = []
        hosts = [server_host]
        if use_office_proxy:
            hosts.insert(0, squid_host)
        for h in hosts:
            ip = _resolve_host(h)
            if ip:
                exclude_ips.append(ip)

        # Prefer underlay NIC toward office proxy (or VPS) so TUN cannot steal dials.
        bind_target = exclude_ips[0] if exclude_ips else (squid_host or server_host)
        bind_iface = detect_bind_interface(bind_target) if bind_target else ""
        if bind_iface:
            self.log(
                f"underlay NIC: {bind_iface} — outbound и direct сидят на ней, "
                f"auto_detect выкл, exclude={', '.join(exclude_ips) or 'нет'}"
            )
        elif enable_tun:
            self.log("underlay NIC: не определён — outbound может уйти в TUN")

        office = bool(squid_host)
        dial = choose_dial(transport, office=office)
        hy = hysteria2_opts(transport) if dial == "hysteria2" else None
        awg = amneziawg_opts(transport) if dial == "amneziawg" else None
        if awg and not enable_tun and not use_office_proxy:
            self.log(
                f"дом: AmneziaWG UDP :{awg['port']} (обфускация handshake)"
            )
            return self._minimal_awg_config(
                server_host=server_host,
                awg=awg,
                bind_iface=bind_iface,
                socks_port=socks_port,
                http_port=http_port,
            )
        if hy and not enable_tun and not use_office_proxy:
            if dial == "hysteria2":
                self.log(
                    f"дом: Hysteria2 UDP :{hy['port']} (Reality на этом Wi-Fi режет DPI)"
                )
            return self._minimal_hy2_config(
                server_host=server_host,
                hy=hy,
                bind_iface=bind_iface,
                socks_port=socks_port,
                http_port=http_port,
            )

        route_exclude = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "224.0.0.0/4",
        ]
        # Public VPS (and office Squid) must stay on the underlay NIC.
        # Without this, Windows auto_route steals VLESS and DNS dies.
        for ip in exclude_ips:
            cidr = f"{ip}/32"
            if cidr not in route_exclude:
                route_exclude.append(cidr)
        proc_names = [
            "sing-box",
            "sing-box.exe",
            "sing-box-awg",
            "ergoms-tun.exe",
            "ergoms-tun-awg.exe",
            f"{APP_EXE}.exe",
            *[f"{name}.exe" for name in APP_EXE_LEGACY],
        ]
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

        rules: list[dict[str, Any]] = []
        # Dial Squid / avoid looping VPS:443 through TUN.
        # Office RST-kills :21114/:21116 on the VPS IP — send those via VLESS :443.
        office = bool(squid_host)
        dial = choose_dial(transport, office=office)
        hy = hysteria2_opts(transport) if dial == "hysteria2" else None
        awg = amneziawg_opts(transport) if dial == "amneziawg" else None
        vpn_port = int(
            (awg or {}).get("port")
            or (hy or {}).get("port")
            or transport.get("port")
            or 443
        )
        if dial == "amneziawg" and awg:
            self.log(f"дом: AmneziaWG UDP :{awg['port']}")
        elif dial == "hysteria2" and hy:
            self.log(f"дом: Hysteria2 UDP :{hy['port']} (Reality на этом Wi-Fi режет DPI)")
        elif office:
            self.log("офис: VLESS+Reality через Squid")
        vps_ip = _resolve_host(server_host)
        if vps_ip:
            rules.append(
                {"ip_cidr": [f"{vps_ip}/32"], "port": vpn_port, "outbound": "direct"}
            )
            rules.append(
                {
                    "ip_cidr": [f"{vps_ip}/32"],
                    "port": RUSTDESK_PORTS,
                    "outbound": "proxy",
                }
            )
            # Office RST on VPS :22. Reverse SSH and ssh-to-VPS must go via VLESS.
            ssh_ports = [int(p) for p in (vps_proxy_ports or [22]) if 1 <= int(p) <= 65535]
            if ssh_ports:
                rules.append(
                    {
                        "ip_cidr": [f"{vps_ip}/32"],
                        "port": ssh_ports if len(ssh_ports) > 1 else ssh_ports[0],
                        "outbound": "proxy",
                    }
                )
        if exclude_ips:
            rules.append(
                {"ip_cidr": [f"{ip}/32" for ip in exclude_ips], "outbound": "direct"}
            )
        bypass_suffixes, bypass_domains = bypass_to_singbox(bypass_hosts or [])
        if bypass_suffixes:
            rules.append({"domain_suffix": bypass_suffixes, "outbound": "direct"})
        if bypass_domains:
            rules.append({"domain": bypass_domains, "outbound": "direct"})
        rules.append({"port": 53, "action": "hijack-dns"})
        # HTTP/SOCKS inbounds (Docker Desktop httpproxy, git, curl) must use
        # VLESS. process_name docker→direct would steal CONNECT and send it
        # out the office NIC.
        rules.append({"inbound": ["socks-in", "http-in"], "outbound": "proxy"})
        rules.append({"process_name": docker_wsl_procs, "outbound": "direct"})
        rules.append({"process_name": proc_names, "outbound": "direct"})
        py_paths = _direct_python_paths()
        if py_paths:
            rules.append({"process_path": py_paths, "outbound": "direct"})
        rules.append({"ip_is_private": True, "outbound": "direct"})

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        mtu_val = max(1280, min(1500, int(mtu)))
        sni = str(transport["server_name"])
        vless_port = int(transport["port"])

        inbounds: list[dict[str, Any]] = [
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
        if enable_tun:
            inbounds.append(
                {
                    "type": "tun",
                    "tag": "tun-in",
                    "interface_name": TUN_IFACE_NAME,
                    "address": ["172.19.0.1/30"],
                    "mtu": mtu_val,
                    # Windows auto_route steals VPS UDP (Hysteria2 QUIC dies)
                    # while SOCKS CONNECT still looks fine. We add 0.0.0.0/1
                    # ourselves after the adapter is up, with VPS /32 pinned.
                    "auto_route": sys.platform != "win32",
                    "strict_route": bool(kill_switch) and sys.platform != "win32",
                    "stack": "mixed" if sys.platform == "win32" else "system",
                    "route_exclude_address": route_exclude,
                }
            )

        box: dict[str, Any] = {
            "log": {
                "level": "info",
                "timestamp": True,
                "output": str(self.log_path).replace("\\", "/"),
            },
            "dns": {
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
                    {
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
            "inbounds": inbounds,
            "outbounds": [
                *(
                    [
                        {
                            "type": "http",
                            "tag": "squid",
                            "server": squid_host,
                            "server_port": int(squid_port),
                            **({"bind_interface": bind_iface} if bind_iface else {}),
                        }
                    ]
                    if use_office_proxy
                    else []
                ),
                (
                    hy2_outbound(server_host, hy, bind_iface=bind_iface)
                    if hy
                    else {
                        "type": "vless",
                        "tag": "proxy",
                        "server": server_host,
                        "server_port": vless_port,
                        "uuid": transport["uuid"],
                        "flow": "xtls-rprx-vision",
                        "packet_encoding": "xudp",
                        "tls": {
                            "enabled": True,
                            "server_name": sni,
                            "utls": {"enabled": True, "fingerprint": "chrome"},
                            "reality": {
                                "enabled": True,
                                "public_key": transport["public_key"],
                                "short_id": transport["short_id"],
                            },
                        },
                        **({"detour": "squid"} if use_office_proxy else {}),
                        **(
                            {"bind_interface": bind_iface}
                            if bind_iface and not use_office_proxy
                            else {}
                        ),
                    }
                ),
                {
                    "type": "direct",
                    "tag": "direct",
                    **({"bind_interface": bind_iface} if bind_iface else {}),
                },
                {"type": "block", "tag": "block"},
            ],
            "route": {
                "auto_detect_interface": not bool(bind_iface),
                **({"default_interface": bind_iface} if bind_iface else {}),
                "final": "proxy",
                "rules": [
                    {"inbound": ["socks-in", "http-in"], "action": "sniff", "timeout": "1s"},
                    *(
                        [{"inbound": ["tun-in"], "action": "sniff", "timeout": "1s"}]
                        if enable_tun
                        else []
                    ),
                    *rules,
                ],
            },
        }
        if awg:
            box["dns"] = _dns_v12(
                docker_wsl_procs=docker_wsl_procs,
                bypass_suffixes=bypass_suffixes,
                bypass_domains=bypass_domains,
            )
            box["endpoints"] = [
                awg_endpoint(server_host, awg, bind_iface=bind_iface)
            ]
            box["outbounds"] = [
                {
                    "type": "direct",
                    "tag": "direct",
                    **({"bind_interface": bind_iface} if bind_iface else {}),
                }
            ]
            box["route"]["default_domain_resolver"] = "dns-local"
        return box

    def _minimal_hy2_config(
        self,
        *,
        server_host: str,
        hy: dict[str, Any],
        bind_iface: str,
        socks_port: int,
        http_port: int,
    ) -> dict[str, Any]:
        """Same shape as sandbox Hy2: extra process/VPS rules break QUIC handshake."""
        self.log("дом: Hy2 как в песочнице — без process/python route на handshake")
        if str(hy.get("obfs_password") or "").strip():
            self.log("дом: Hy2 salamander — QUIC Initial без открытого SNI")
        bind = _udp_bind(bind_iface)
        ip = str(bind.get("inet4_bind_address") or "")
        if ip:
            self.log(
                f"дом: Hy2 UDP с {ip} (имя NIC на Windows глотает датаграммы)"
            )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        outbound = hy2_outbound(server_host, hy, bind_iface=bind_iface)
        return {
            "log": {
                "level": "info",
                "timestamp": True,
                "output": str(self.log_path).replace("\\", "/"),
            },
            "dns": {
                "servers": [
                    {
                        "tag": "dns-proxy",
                        "address": "https://1.1.1.1/dns-query",
                        "detour": "proxy",
                    }
                ],
                "final": "dns-proxy",
                "strategy": "prefer_ipv4",
            },
            "inbounds": [
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
            ],
            "outbounds": [
                outbound,
                {
                    "type": "direct",
                    "tag": "direct",
                    **({"bind_interface": bind_iface} if bind_iface else {}),
                },
            ],
            "route": {
                "auto_detect_interface": False,
                **({"default_interface": bind_iface} if bind_iface else {}),
                "final": "proxy",
            },
        }

    def _minimal_awg_config(
        self,
        *,
        server_host: str,
        awg: dict[str, Any],
        bind_iface: str,
        socks_port: int,
        http_port: int,
    ) -> dict[str, Any]:
        """SOCKS/HTTP only — TUN after handshake, same deferral as Hy2."""
        self.log("дом: AmneziaWG как в песочнице — без TUN на handshake")
        bind = _udp_bind(bind_iface)
        ip = str(bind.get("inet4_bind_address") or "")
        if ip:
            self.log(f"дом: AWG UDP с {ip}")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        return {
            "log": {
                "level": "info",
                "timestamp": True,
                "output": str(self.log_path).replace("\\", "/"),
            },
            "dns": _dns_v12(),
            "inbounds": [
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
            ],
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
        mtu: int = 1400,
        vps_proxy_ports: list[int] | None = None,
        force_restart: bool = False,
        kill_switch: bool = False,
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
            mtu=mtu,
            vps_proxy_ports=vps_proxy_ports,
            kill_switch=kill_switch,
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
        hy = hysteria2_opts(transport) if dial == "hysteria2" else None
        awg = amneziawg_opts(transport) if dial == "amneziawg" else None
        if awg:
            dest = f"AmneziaWG {server_host}:{awg['port']}/udp"
        elif hy:
            dest = f"Hysteria2 {server_host}:{hy['port']}/udp"
        else:
            dest = f"VLESS {server_host}:{transport['port']}"
        self.log(
            f"Starting MODE=singbox → {dest}"
            f"{' via proxy' if office else ''}"
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

        if self._wait_socks(socks_port, http_port, enable_tun=enable_tun, pid=pid):
            return
        if enable_tun and self._tun_adapter_busy():
            self.log("TUN-адаптер ещё занят — жду и пробую ещё раз")
            time.sleep(1.5)
            leftover = self.pid() or pid
            if leftover and procutil.pid_alive(leftover):
                procutil.kill_pids([leftover])
            pid = self._launch(
                exe, elevate=need_admin, prelude_cmds=prelude, postlude_cmds=postlude
            )
            if pid:
                self.pid_path.write_text(str(pid), encoding="utf-8")
                self.log(f"sing-box pid={pid} (повтор)")
            if self._wait_socks(socks_port, http_port, enable_tun=enable_tun, pid=pid):
                return

        self._log_tail("не поднялся SOCKS")
        raise RuntimeError(
            f"sing-box не открыл SOCKS :{socks_port}. "
            f"См. {self.log_path}. Нужен download-sing-box и верные transport.*"
        )

    def tail_log(self, n: int = 20) -> list[str]:
        if not self.log_path.is_file():
            return []
        try:
            size = self.log_path.stat().st_size
            with self.log_path.open("rb") as fh:
                if size > 65536:
                    fh.seek(size - 65536)
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
        text = "\n".join(self.tail_log(40)).lower()
        return "configure tun interface" in text or "wintun" in text

    def _wait_socks(
        self,
        socks_port: int,
        http_port: int,
        *,
        enable_tun: bool,
        pid: int | None,
        timeout: float = 6.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        interval = 0.05
        while time.monotonic() < deadline:
            if _port_open("127.0.0.1", socks_port):
                kind = "mixed + TUN" if enable_tun else "mixed"
                self.log(
                    f"sing-box слушает SOCKS :{socks_port} и HTTP :{http_port} ({kind})"
                )
                return True
            check = pid or self.pid()
            if check and not procutil.pid_alive(check) and not self.pid():
                return False
            time.sleep(interval)
            interval = min(interval * 1.3, 0.2)
        return False

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
        if not targets:
            self.log("sing-box уже не запущен")
            self.pid_path.unlink(missing_ok=True)
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

    def _launch(
        self,
        exe: Path,
        *,
        elevate: bool,
        prelude_cmds: list[str] | None = None,
        postlude_cmds: list[str] | None = None,
    ) -> int | None:
        args = [str(exe), "run", "-c", str(self.config_path)]
        prelude = [c for c in (prelude_cmds or []) if c]
        postlude = [c for c in (postlude_cmds or []) if c]
        if sys.platform == "win32" and elevate and not procutil.is_admin():
            return self._start_elevated_win(
                exe, self.config_path, prelude, postlude
            )

        if (
            sys.platform != "win32"
            and elevate
            and hasattr(os, "geteuid")
            and os.geteuid() != 0
        ):
            return self._start_elevated_linux(args, prelude)

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=procutil.creationflags(),
        )
        if postlude:
            threading.Thread(
                target=self._run_postlude,
                args=(list(postlude),),
                daemon=True,
            ).start()
        return proc.pid

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
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
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


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
