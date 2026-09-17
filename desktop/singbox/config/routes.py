from desktop.singbox.manager import (
    _build_route_rules as build_route_rules,
)
from desktop.singbox.manager import (
    choose_dial,
    dial_label,
    effective_tun_mtu,
    parse_corporate_proxy,
    require_transport,
    resolve_dial_bundle,
    rustdesk_hairpin_rules,
    underlay_bind_target,
    underlay_forget_hosts,
    underlay_keep_hosts,
)

__all__ = [
    "build_route_rules",
    "choose_dial",
    "dial_label",
    "effective_tun_mtu",
    "parse_corporate_proxy",
    "require_transport",
    "resolve_dial_bundle",
    "rustdesk_hairpin_rules",
    "underlay_bind_target",
    "underlay_forget_hosts",
    "underlay_keep_hosts",
]
