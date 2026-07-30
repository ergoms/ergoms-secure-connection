#!/usr/bin/env python3
"""HTTP CONNECT shim for OpenSSH ProxyCommand through a corporate proxy.

Usage:
  ssh -o ProxyCommand="python connect_proxy.py %h %p" ...

Env:
  OPS_CONTENT_HTTP_PROXY   default: 192.0.2.10:3128
"""

from __future__ import annotations

import os
import select
import socket
import sys
import threading


def parse_proxy(value: str) -> tuple[str, int]:
    value = value.replace("http://", "").replace("https://", "").strip("/")
    host, _, port = value.partition(":")
    return host, int(port or "3128")


def open_connect(target_host: str, target_port: int) -> socket.socket:
    proxy_host, proxy_port = parse_proxy(
        os.environ.get("OPS_CONTENT_HTTP_PROXY", "192.0.2.10:3128")
    )
    sock = socket.create_connection((proxy_host, proxy_port), timeout=30)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    req = (
        f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
        f"Host: {target_host}:{target_port}\r\n"
        f"Proxy-Connection: keep-alive\r\n"
        f"\r\n"
    ).encode("ascii")
    sock.sendall(req)

    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("proxy closed during CONNECT")
        buf += chunk

    header, _, remainder = buf.partition(b"\r\n\r\n")
    status_line = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
    if "200" not in status_line:
        raise RuntimeError(f"CONNECT failed: {status_line}")

    if remainder:
        # Keep early payload, if any.
        sock = _PrefixedSocket(sock, remainder)
    return sock


class _PrefixedSocket:
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


def pump_windows(sock: socket.socket) -> int:
    # BufferedReader.read(n) on Windows pipes often waits for n bytes — SSH
    # packets are small, so KEX stalls after the banner. Use raw unbuffered IO.
    stdin = open(sys.stdin.fileno(), "rb", closefd=False, buffering=0)
    stdout = open(sys.stdout.fileno(), "wb", closefd=False, buffering=0)
    errors: list[BaseException] = []

    def sock_to_stdout() -> None:
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                stdout.write(data)
                stdout.flush()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            try:
                stdin.close()
            except Exception:  # noqa: BLE001
                pass

    def stdin_to_sock() -> None:
        try:
            while True:
                data = stdin.read(65536)
                if not data:
                    break
                sock.sendall(data)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t1 = threading.Thread(target=sock_to_stdout, daemon=True)
    t2 = threading.Thread(target=stdin_to_sock, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    return 1 if errors else 0


def pump_posix(sock: socket.socket) -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        readable, _, _ = select.select([sock, stdin], [], [])
        if sock in readable:
            data = sock.recv(65536)
            if not data:
                return 0
            stdout.write(data)
            stdout.flush()
        if stdin in readable:
            data = stdin.read(65536)
            if not data:
                return 0
            sock.sendall(data)


def _allowed_target(host: str, port: int) -> bool:
    """Optional allowlist: OPS_CONTENT_CONNECT_ALLOW=host:port[,host2:port2]."""
    raw = (os.environ.get("OPS_CONTENT_CONNECT_ALLOW") or "").strip()
    if not raw:
        return True
    want = f"{host.lower()}:{port}"
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part == want:
            return True
        # allow host-only entry matching any port (rare)
        if ":" not in part and part == host.lower():
            return True
    return False


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: connect_proxy.py <host> <port>", file=sys.stderr)
        return 2

    target_host = sys.argv[1]
    target_port = int(sys.argv[2])

    if not _allowed_target(target_host, target_port):
        print(
            f"CONNECT denied: {target_host}:{target_port} not in OPS_CONTENT_CONNECT_ALLOW",
            file=sys.stderr,
        )
        return 1

    try:
        sock = open_connect(target_host, target_port)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    if os.name == "nt":
        return pump_windows(sock)
    return pump_posix(sock)


if __name__ == "__main__":
    raise SystemExit(main())
