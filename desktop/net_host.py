"""Host resolve + underlay gateway without tun ↔ kill_switch import cycles."""

from __future__ import annotations

import socket


def resolve_host(host: str) -> str | None:
    host = (host or "").strip()
    if not host:
        return None
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def underlay_gateway(dest: str) -> str | None:
    from desktop.kill_switch import underlay_gateway as _gateway

    return _gateway(dest)
