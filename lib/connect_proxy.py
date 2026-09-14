#!/usr/bin/env python3
"""HTTP CONNECT shim for OpenSSH ProxyCommand through a corporate proxy.

Usage:
  ssh -o ProxyCommand="python connect_proxy.py %h %p" ...

Env:
  ERGOMS_SC_HTTP_PROXY   default: 192.0.2.10:3128
"""

from __future__ import annotations

import os
import select
import socket
import sys
import threading

try:
    from lib.http_connect import http_connect, parse_proxy
except ImportError:
    from http_connect import http_connect, parse_proxy


def open_connect(target_host: str, target_port: int) -> socket.socket:
    proxy_host, proxy_port = parse_proxy(
        os.environ.get("ERGOMS_SC_HTTP_PROXY")
        or os.environ.get("OPS_CONTENT_HTTP_PROXY", "192.0.2.10:3128")
    )
    try:
        sock, _status = http_connect(
            proxy_host,
            proxy_port,
            target_host,
            target_port,
            timeout=30,
            keep_remainder=True,
        )
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    return sock


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
    # Same pitfall as Windows: BufferedReader.read(n) on a pipe issues a second
    # raw read that blocks until more data or EOF. OpenSSH writes a short banner
    # then waits for the remote banner on our stdout → deadlock / banner timeout.
    stdin = open(sys.stdin.fileno(), "rb", closefd=False, buffering=0)
    stdout = open(sys.stdout.fileno(), "wb", closefd=False, buffering=0)
    rlist: list = [sock, stdin]
    while rlist:
        readable, _, _ = select.select(rlist, [], [])
        if sock in readable:
            data = sock.recv(65536)
            if not data:
                return 0
            try:
                stdout.write(data)
                stdout.flush()
            except BrokenPipeError:
                return 1
        if stdin in readable:
            data = stdin.read(65536)
            if not data:
                # SSH closed stdin; half-close toward the server, keep reading sock.
                rlist = [sock]
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            sock.sendall(data)


def _allowed_target(host: str, port: int) -> bool:
    """Optional allowlist: ERGOMS_SC_CONNECT_ALLOW=host:port[,host2:port2]."""
    raw = (
        os.environ.get("ERGOMS_SC_CONNECT_ALLOW")
        or os.environ.get("OPS_CONTENT_CONNECT_ALLOW")
        or ""
    ).strip()
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
            f"CONNECT denied: {target_host}:{target_port} not in ERGOMS_SC_CONNECT_ALLOW",
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
