"""HTTP CONNECT through a forward proxy (Squid / corporate)."""

from __future__ import annotations

import socket

try:
    from lib.netutil import parse_endpoint, read_http_headers, tune_tcp
except ImportError:
    from netutil import parse_endpoint, read_http_headers, tune_tcp


def parse_proxy(value: str, default_port: int = 3128) -> tuple[str, int]:
    return parse_endpoint(value, default_port)


class PrefixedSocket:
    """Socket wrapper that yields leftover bytes after the CONNECT response."""

    def __init__(self, sock: socket.socket, prefix: bytes) -> None:
        self._sock = sock
        self._prefix = prefix

    def recv(self, n: int) -> bytes:
        if self._prefix:
            out, self._prefix = self._prefix[:n], self._prefix[n:]
            return out
        return self._sock.recv(n)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def shutdown(self, how: int) -> None:
        self._sock.shutdown(how)

    def close(self) -> None:
        self._sock.close()

    def fileno(self) -> int:
        return self._sock.fileno()


def http_connect(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    *,
    timeout: float = 30,
    nodelay: bool = True,
    buffers: bool = False,
    keep_remainder: bool = False,
    require_spaced_200: bool = False,
) -> tuple[socket.socket, str]:
    """Return (socket, status_line). Raises OSError if the proxy refuses CONNECT."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    tune_tcp(sock, buffers=buffers, nodelay=nodelay)
    req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Proxy-Connection: keep-alive\r\n"
        f"\r\n"
    ).encode("ascii")
    sock.sendall(req)
    buf = read_http_headers(sock)
    if not buf:
        sock.close()
        raise OSError("proxy closed during CONNECT")
    header, _, remainder = buf.partition(b"\r\n\r\n")
    status = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
    ok = (" 200 " in status) if require_spaced_200 else ("200" in status)
    if not ok:
        sock.close()
        raise OSError(f"CONNECT failed: {status}")
    if keep_remainder and remainder:
        return PrefixedSocket(sock, remainder), status  # type: ignore[return-value]
    return sock, status
