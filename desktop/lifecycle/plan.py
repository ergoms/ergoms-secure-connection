"""One-shot connect plan. Dial / TUN / kill-switch heuristics live here only."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from desktop.config_io import (
    get_http_bridge_port,
    get_kill_switch,
    get_local_socks_port,
    get_server,
    get_server_host,
    get_sing_box_path,
    get_socks_scope,
    get_tun_elevate,
    get_tun_enabled,
    get_tun_mtu,
    get_vps_proxy_ports,
    resolve_corporate_proxy,
)
from desktop.kill_switch import allow_ips as kill_switch_allow_ips
from desktop.singbox_mode import (
    choose_dial,
    require_transport,
    resolve_dial_bundle,
    underlay_forget_hosts,
    underlay_keep_hosts,
)


@dataclass(frozen=True)
class ConnectPlan:
    host: str
    port: int
    dial: str
    awg: dict[str, Any] | None
    office_proxy: str
    transport: dict[str, Any]
    socks_port: int
    http_port: int
    sing_box_path: str
    bypass: list[str]
    vpn_hosts: list[str]
    mtu: int
    vps_proxy_ports: list[int]
    elevate: bool
    scope: str
    tun_wanted: bool
    tun_deferred: bool
    start_tun: bool
    ks_wanted: bool
    ks_deferred: bool
    start_ks: bool
    allow: list[str] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)

    @property
    def udp_dial(self) -> bool:
        return self.dial == "amneziawg"


def resolve_connect_plan(
    cfg: dict[str, Any],
    *,
    platform: str | None = None,
    tun_enabled: bool | None = None,
    kill_switch: bool | None = None,
) -> ConnectPlan:
    plat = sys.platform if platform is None else platform
    server = get_server(cfg)
    host = get_server_host(cfg)
    if "YOUR_VPS" in host or not host:
        raise RuntimeError("Set real server.host in config.json")
    transport = require_transport(cfg)
    port = int(transport.get("port") or server.get("port") or 443)
    office_proxy = resolve_corporate_proxy(cfg)
    dial = choose_dial(transport, office=bool(office_proxy))
    _dial, awg = resolve_dial_bundle(transport, office=bool(office_proxy))
    if dial == "amneziawg" and not awg:
        raise RuntimeError("AmneziaWG: укажите ключи в Настройках")
    ks = get_kill_switch(cfg) if kill_switch is None else bool(kill_switch)
    tun = get_tun_enabled(cfg) if tun_enabled is None else bool(tun_enabled)
    enable_tun = bool(tun or ks)
    udp_dial = dial == "amneziawg"
    defer_win = plat == "win32" and (not bool(office_proxy) or udp_dial)
    ks_deferred = bool(defer_win and ks and udp_dial)
    tun_deferred = bool(awg and enable_tun)
    start_tun = enable_tun and not tun_deferred
    start_ks = ks and not ks_deferred and not tun_deferred
    bypass = [str(h) for h in cfg.get("proxy_bypass") or [] if h]
    if not bypass:
        bypass = ["*.local", "*.lan"]
    vpn_hosts = [str(h) for h in cfg.get("blocked_hosts") or [] if h]
    allow = kill_switch_allow_ips(*underlay_keep_hosts(host, office_proxy, udp_dial=udp_dial))
    forget = kill_switch_allow_ips(
        *underlay_forget_hosts(host, office_proxy, udp_dial=udp_dial)
    )
    return ConnectPlan(
        host=host,
        port=port,
        dial=dial,
        awg=awg,
        office_proxy=office_proxy,
        transport=transport,
        socks_port=get_local_socks_port(cfg),
        http_port=get_http_bridge_port(),
        sing_box_path=get_sing_box_path(cfg),
        bypass=bypass,
        vpn_hosts=vpn_hosts,
        mtu=get_tun_mtu(cfg),
        vps_proxy_ports=get_vps_proxy_ports(cfg),
        elevate=get_tun_elevate(cfg),
        scope=get_socks_scope(cfg),
        tun_wanted=enable_tun,
        tun_deferred=tun_deferred,
        start_tun=start_tun,
        ks_wanted=ks,
        ks_deferred=ks_deferred,
        start_ks=start_ks,
        allow=allow,
        forget=forget,
    )
