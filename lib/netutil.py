"""Tiny TCP helpers shared by client, sing-box, kill switch, and watchdog."""

from __future__ import annotations

import socket


def port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tune_tcp(sock: socket.socket, *, buffers: bool = False, nodelay: bool = True) -> None:
    if nodelay:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    if not buffers:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    except OSError:
        pass


def parse_endpoint(
    value: str,
    default_port: int,
    *,
    default_host: str = "",
    strip_schemes: tuple[str, ...] = (),
) -> tuple[str, int]:
    raw = (value or "").strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    for scheme in strip_schemes:
        raw = raw.replace(scheme, "")
    raw = raw.strip("/")
    host, _, port = raw.partition(":")
    host = host.strip() or default_host
    return host, int(port or str(default_port))


def read_http_headers(sock: socket.socket, *, max_bytes: int = 8192) -> bytes:
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < max_bytes:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf
