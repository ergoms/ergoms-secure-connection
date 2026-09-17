"""sing-box config, process, spawn, and readiness."""

from desktop.singbox.manager import (  # noqa: F401
    SingboxModeManager,
    amneziawg_opts,
    awg_endpoint,
    choose_dial,
    config_skeleton,
    dial_label,
    dns_block,
    effective_tun_mtu,
    local_inbounds,
    parse_corporate_proxy,
    require_transport,
    resolve_dial_bundle,
    rustdesk_hairpin_rules,
    underlay_bind_target,
    underlay_forget_hosts,
    underlay_keep_hosts,
)

__all__ = [
    "SingboxModeManager",
    "amneziawg_opts",
    "awg_endpoint",
    "choose_dial",
    "config_skeleton",
    "dial_label",
    "dns_block",
    "effective_tun_mtu",
    "local_inbounds",
    "parse_corporate_proxy",
    "require_transport",
    "resolve_dial_bundle",
    "rustdesk_hairpin_rules",
    "underlay_bind_target",
    "underlay_forget_hosts",
    "underlay_keep_hosts",
]
