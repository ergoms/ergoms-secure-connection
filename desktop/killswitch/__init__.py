"""Kill-switch routes, pin, detect, persist."""

from desktop.killswitch._impl import (  # noqa: F401
    allow_ips,
    apply,
    clear,
    install_commands,
    is_applied,
    is_sealed,
    lift_ipv4_blackhole_commands,
    lift_ipv4_blackholes,
    pin_underlay,
    planned_pin_commands,
    prefer_tun_ipv4,
    remember_plan,
    state_path,
    suppress_underlay_ipv6,
    underlay_gateway,
)

__all__ = [
    "allow_ips",
    "apply",
    "clear",
    "is_applied",
    "is_sealed",
    "pin_underlay",
    "remember_plan",
    "state_path",
]
