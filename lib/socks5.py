"""SOCKS5 CONNECT handshake (ProxyCommand, HTTP-via-SOCKS, probes)."""

from __future__ import annotations

import socket
import struct

try:
    from lib.netutil import tune_tcp
except ImportError:
    from netutil import tune_tcp


def consume_bind_addr(sock: socket.socket, atyp: int) -> None:
    if atyp == 1:
        sock.recv(4 + 2)
        return
    if atyp == 3:
        ln = sock.recv(1)
        if not ln:
            raise OSError("SOCKS5 CONNECT bind truncated")
        sock.recv(ln[0] + 2)
        return
    if atyp == 4:
        sock.recv(16 + 2)
        return
    raise OSError(f"SOCKS5 bad atyp={atyp}")


def _connect_request(host: str, port: int, *, prefer_ipv4_atyp: bool, encoding: str) -> bytes:
    if prefer_ipv4_atyp:
        try:
            return b"\x05\x01\x00\x01" + socket.inet_aton(host) + struct.pack("!H", port)
        except OSError:
            pass
    host_b = host.encode(encoding)
    if len(host_b) > 255:
        raise OSError(f"host too long for SOCKS5: {host}")
    return b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack("!H", port)


def handshake(
    sock: socket.socket,
    host: str,
    port: int,
    *,
    prefer_ipv4_atyp: bool = False,
    encoding: str = "idna",
) -> None:
    """Greeting + CONNECT. Raises OSError on failure."""
    sock.sendall(b"\x05\x01\x00")
    greet = sock.recv(2)
    if len(greet) != 2 or greet[0] != 5 or greet[1] != 0:
        raise OSError(f"SOCKS5 greeting failed: {greet!r}")
    sock.sendall(
        _connect_request(host, port, prefer_ipv4_atyp=prefer_ipv4_atyp, encoding=encoding)
    )
    hdr = sock.recv(4)
    if len(hdr) != 4:
        raise OSError("SOCKS5 CONNECT truncated")
    if hdr[0] != 5:
        raise OSError(f"SOCKS5 CONNECT truncated: {hdr!r}")
    if hdr[1] != 0:
        raise OSError(f"SOCKS5 CONNECT rejected: rep={hdr[1]}")
    consume_bind_addr(sock, hdr[3])


def connect(
    socks_host: str,
    socks_port: int,
    host: str,
    port: int,
    *,
    timeout: float = 30,
    tune: bool = False,
    buffers: bool = False,
    prefer_ipv4_atyp: bool = False,
    encoding: str = "idna",
) -> socket.socket:
    sock = socket.create_connection((socks_host, socks_port), timeout=timeout)
    sock.settimeout(timeout)
    if tune:
        tune_tcp(sock, buffers=buffers)
    try:
        handshake(
            sock,
            host,
            port,
            prefer_ipv4_atyp=prefer_ipv4_atyp,
            encoding=encoding,
        )
    except Exception:
        sock.close()
        raise
    return sock
