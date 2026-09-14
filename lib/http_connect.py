"""HTTP CONNECT through a forward proxy (Squid / corporate)."""

from __future__ import annotations

import socket


def parse_proxy(value: str, default_port: int = 3128) -> tuple[str, int]:
    value = (value or "").replace("http://", "").replace("https://", "").strip("/")
    host, _, port = value.partition(":")
    return host.strip(), int(port or str(default_port))


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


def _tune(sock: socket.socket, *, nodelay: bool, buffers: bool) -> None:
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
    _tune(sock, nodelay=nodelay, buffers=buffers)
    req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Proxy-Connection: keep-alive\r\n"
        f"\r\n"
    ).encode("ascii")
    sock.sendall(req)
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < 8192:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
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
