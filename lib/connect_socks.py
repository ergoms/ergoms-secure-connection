#!/usr/bin/env python3
"""SSH ProxyCommand via local SOCKS5 (ERGOMS SECURE CONNECTION :1080).

Usage:
  ssh -o ProxyCommand="python connect_socks.py %h %p" ...

Env:
  ERGOMS_SC_SOCKS   default: 127.0.0.1:1080
"""

from __future__ import annotations

import os
import sys

try:
    from lib.connect_proxy import pump_posix, pump_windows
    from lib.socks5 import connect as socks5_connect
except ImportError:
    from connect_proxy import pump_posix, pump_windows
    from socks5 import connect as socks5_connect


def parse_socks(value: str) -> tuple[str, int]:
    value = value.replace("socks5://", "").replace("socks5h://", "").strip()
    host, _, port = value.partition(":")
    return host or "127.0.0.1", int(port or "1080")


def open_socks(target_host: str, target_port: int):
    socks_host, socks_port = parse_socks(
        os.environ.get("ERGOMS_SC_SOCKS")
        or os.environ.get("OPS_CONTENT_SOCKS", "127.0.0.1:1080")
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
