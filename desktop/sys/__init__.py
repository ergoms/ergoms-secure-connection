"""OS-level helpers shared by tun, kill-switch, and sing-box."""

from desktop.sys.constants import (
    DEFAULT_NO_PROXY,
    DOCKER_DESKTOP_HOST_GATEWAY,
    DOCKER_NO_PROXY,
    SINGBOX_PROCESS_NAMES,
)
from desktop.sys.win_net import (
    NetshIface,
    best_interface_index,
    netsh_ipv4_interfaces,
    route_print_v4,
    run_route_lines,
)

__all__ = [
    "DEFAULT_NO_PROXY",
    "DOCKER_DESKTOP_HOST_GATEWAY",
    "DOCKER_NO_PROXY",
    "SINGBOX_PROCESS_NAMES",
    "NetshIface",
    "best_interface_index",
    "netsh_ipv4_interfaces",
    "route_print_v4",
    "run_route_lines",
]
