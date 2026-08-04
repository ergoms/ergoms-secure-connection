#!/usr/bin/env python3
"""Test whether Squid allows HTTP CONNECT to host:port."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path


def load_proxy(default: str = "192.0.2.10:3128") -> tuple[str, int]:
    cfg_path = Path(__file__).resolve().parent / "config.json"
    value = os.environ.get("OPS_CONTENT_HTTP_PROXY", "")
    if not value and cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        value = cfg.get("corporate_proxy") or default
    value = (value or default).replace("http://", "").replace("https://", "").strip("/")
    host, _, port = value.partition(":")
    return host, int(port or "3128")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", nargs="?", type=int, default=443)
    args = ap.parse_args()

    proxy_host, proxy_port = load_proxy()
    print(f"proxy={proxy_host}:{proxy_port}")
    print(f"target={args.host}:{args.port}")

    sock = socket.create_connection((proxy_host, proxy_port), timeout=5)
    req = (
        f"CONNECT {args.host}:{args.port} HTTP/1.1\r\n"
        f"Host: {args.host}:{args.port}\r\n"
        f"Proxy-Connection: keep-alive\r\n\r\n"
    ).encode("ascii")
    sock.sendall(req)

    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < 8192:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    sock.close()

    status = buf.split(b"\r\n", 1)[0].decode("ascii", "replace")
    print(status)
    if "200" in status:
        print("OK: Squid allows CONNECT to this host:port")
        print("You can use SSH tunnel (start) or HTTPS relay on this host.")
        return 0
    print("FAIL: Squid denied/failed CONNECT")
    if args.port == 22:
        print("Tip: try port 443 (run sshd on 443 or reverse-proxy SSH).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
