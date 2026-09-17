"""sing-box JSON builders. Symbols live in manager until callers migrate."""

from desktop.singbox.manager import (
    _build_route_rules as build_route_rules,
)
from desktop.singbox.manager import (
    _tun_inbound as tun_inbound,
)
from desktop.singbox.manager import (
    _vless_outbound as vless_outbound,
)
from desktop.singbox.manager import (
    amneziawg_opts,
    awg_endpoint,
    awg_peer_address,
    choose_dial,
    config_skeleton,
    dial_label,
    dns_block,
    effective_tun_mtu,
    local_inbounds,
    log_block,
    parse_corporate_proxy,
    require_transport,
    resolve_dial_bundle,
    rustdesk_hairpin_rules,
    underlay_bind_target,
    underlay_forget_hosts,
    underlay_keep_hosts,
)

__all__ = [
    "amneziawg_opts",
    "awg_endpoint",
    "awg_peer_address",
    "build_route_rules",
    "choose_dial",
    "config_skeleton",
    "dial_label",
    "dns_block",
    "effective_tun_mtu",
    "local_inbounds",
    "log_block",
    "parse_corporate_proxy",
    "require_transport",
    "resolve_dial_bundle",
    "rustdesk_hairpin_rules",
    "tun_inbound",
    "underlay_bind_target",
    "underlay_forget_hosts",
    "underlay_keep_hosts",
    "vless_outbound",
]
