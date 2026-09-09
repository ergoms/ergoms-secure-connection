#!/usr/bin/env python3
"""SSH ProxyCommand via local SOCKS5 (ERGOMS VPN :1080).

Usage:
  ssh -o ProxyCommand="python connect_socks.py %h %p" ...

Env:
  ERGOMS_VPN_SOCKS   default: 127.0.0.1:1080
"""

from __future__ import annotations

import os
import socket
import struct
import sys

try:
    from lib.connect_proxy import pump_posix, pump_windows
except ImportError:
    from connect_proxy import pump_posix, pump_windows


def parse_socks(value: str) -> tuple[str, int]:
    value = value.replace("socks5://", "").replace("socks5h://", "").strip()
    host, _, port = value.partition(":")
    return host or "127.0.0.1", int(port or "1080")


def open_socks(target_host: str, target_port: int) -> socket.socket:
    socks_host, socks_port = parse_socks(
        os.environ.get("ERGOMS_VPN_SOCKS")
        or os.environ.get("OPS_CONTENT_SOCKS", "127.0.0.1:1080")
    )
    sock = socket.create_connection((socks_host, socks_port), timeout=20)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass

    sock.sendall(b"\x05\x01\x00")
    greet = sock.recv(2)
    if len(greet) != 2 or greet[0] != 5 or greet[1] != 0:
        sock.close()
        raise RuntimeError(f"SOCKS5 greeting failed: {greet!r}")

    host_b = target_host.encode("idna")
    if len(host_b) > 255:
        sock.close()
        raise RuntimeError(f"host too long for SOCKS5: {target_host}")
    sock.sendall(
        b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack("!H", target_port)
    )
    hdr = sock.recv(4)
    if len(hdr) != 4 or hdr[0] != 5:
        sock.close()
        raise RuntimeError(f"SOCKS5 CONNECT truncated: {hdr!r}")
    if hdr[1] != 0:
        sock.close()
        raise RuntimeError(f"SOCKS5 CONNECT rejected: rep={hdr[1]}")
    atyp = hdr[3]
    if atyp == 1:
        sock.recv(4 + 2)
    elif atyp == 3:
        ln = sock.recv(1)
        if not ln:
            sock.close()
            raise RuntimeError("SOCKS5 CONNECT bind truncated")
        sock.recv(ln[0] + 2)
    elif atyp == 4:
        sock.recv(16 + 2)
    else:
        sock.close()
        raise RuntimeError(f"SOCKS5 bad atyp={atyp}")
    return sock


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: connect_socks.py <host> <port>", file=sys.stderr)
        return 2
    host = sys.argv[1]
    port = int(sys.argv[2])
    try:
        sock = open_socks(host, port)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    if os.name == "nt":
        return pump_windows(sock)
    return pump_posix(sock)


if __name__ == "__main__":
    raise SystemExit(main())
