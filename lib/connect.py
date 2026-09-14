"""OpenSSH ProxyCommand: --via socks (local) or http (corporate CONNECT)."""

from __future__ import annotations

import os
import select
import socket
import sys
import threading

try:
    from lib.http_connect import http_connect
    from lib.netutil import parse_endpoint
    from lib.socks5 import connect as socks5_connect
except ImportError:
    from http_connect import http_connect
    from netutil import parse_endpoint
    from socks5 import connect as socks5_connect


def pump_windows(sock: socket.socket) -> int:
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
    try:
        sock.close()
    except OSError:
        pass
    return 1 if errors else 0


def pump_posix(sock: socket.socket, *, timeout: float = 300.0) -> int:
    stdin = open(sys.stdin.fileno(), "rb", closefd=False, buffering=0)
    stdout = open(sys.stdout.fileno(), "wb", closefd=False, buffering=0)
    rlist: list = [sock, stdin]
    try:
        while rlist:
            readable, _, _ = select.select(rlist, [], [], timeout)
            if not readable:
                return 1
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
                    rlist = [sock]
                    try:
                        sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                sock.sendall(data)
        return 0
    finally:
        try:
            sock.close()
        except OSError:
            pass


def pump(sock: socket.socket) -> int:
    if os.name == "nt":
        return pump_windows(sock)
    return pump_posix(sock)


def open_socks(target_host: str, target_port: int) -> socket.socket:
    socks_host, socks_port = parse_endpoint(
        os.environ.get("ERGOMS_SC_SOCKS")
        or os.environ.get("OPS_CONTENT_SOCKS", "127.0.0.1:1080"),
        1080,
        default_host="127.0.0.1",
        strip_schemes=("socks5://", "socks5h://", "http://", "https://"),
    )
    try:
        return socks5_connect(
            socks_host,
            socks_port,
            target_host,
            target_port,
            timeout=20,
            tune=True,
        )
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc


def open_http(target_host: str, target_port: int) -> socket.socket:
    proxy_host, proxy_port = parse_endpoint(
        os.environ.get("ERGOMS_SC_HTTP_PROXY")
        or os.environ.get("OPS_CONTENT_HTTP_PROXY", "192.0.2.10:3128"),
        3128,
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


def allowed_target(host: str, port: int) -> bool:
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
        if ":" not in part and part == host.lower():
            return True
    return False


def main(argv: list[str] | None = None, *, via: str = "socks") -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "--via":
        via = args[1]
        args = args[2:]
    if via not in ("socks", "http"):
        print("usage: connect.py [--via socks|http] <host> <port>", file=sys.stderr)
        return 2
    if len(args) != 2:
        print("usage: connect.py [--via socks|http] <host> <port>", file=sys.stderr)
        return 2
    host, port_s = args[0], args[1]
    try:
        port = int(port_s)
    except ValueError:
        print(f"bad port: {port_s}", file=sys.stderr)
        return 2
    if via == "http" and not allowed_target(host, port):
        print(
            f"CONNECT denied: {host}:{port} not in ERGOMS_SC_CONNECT_ALLOW",
            file=sys.stderr,
        )
        return 1
    try:
        sock = open_http(host, port) if via == "http" else open_socks(host, port)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return pump(sock)


if __name__ == "__main__":
    raise SystemExit(main())
