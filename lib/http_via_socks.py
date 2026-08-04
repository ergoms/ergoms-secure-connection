#!/usr/bin/env python3
"""HTTP proxy via local SOCKS5 + PAC (full / github) with bypass list.

Security:
  - listens on loopback only (caller binds 127.0.0.1)
  - refuses private / link-local / metadata destinations (SSRF via VPS)
  - PAC keeps those ranges off the tunnel for browsers
"""

from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import select
import socket
import struct
import threading
from urllib.parse import urlsplit

# Hostnames that must never go through the VPS tunnel
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)


def _tune(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    except OSError:
        pass


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))  # CGNAT
        or (ip.version == 6 and ip in ipaddress.ip_network("fc00::/7"))  # ULA
    )


def is_blocked_destination(host: str) -> bool:
    """True if CONNECT/HTTP via SOCKS would risk SSRF into private nets / metadata."""
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return True
    if h in _BLOCKED_HOSTNAMES:
        return True
    if h.endswith(".localhost") or h.endswith(".local") or h.endswith(".internal"):
        return True
    if h.startswith("metadata.") or "metadata.google" in h:
        return True
    try:
        ip = ipaddress.ip_address(h)
        return _is_blocked_ip(ip)
    except ValueError:
        pass
    # Reject obvious dotted quads that failed parse (malformed)
    if h.replace(".", "").isdigit() and h.count(".") == 3:
        return True
    return False


def socks5_connect(socks_host: str, socks_port: int, host: str, port: int) -> socket.socket:
    if is_blocked_destination(host):
        raise OSError(f"destination blocked (private/metadata): {host}")
    if not (1 <= port <= 65535):
        raise OSError(f"bad port: {port}")

    s = socket.create_connection((socks_host, socks_port), timeout=30)
    _tune(s)
    s.sendall(b"\x05\x01\x00")
    resp = s.recv(2)
    if len(resp) != 2 or resp[0] != 5 or resp[1] != 0:
        s.close()
        raise OSError(f"SOCKS5 auth failed: {resp!r}")

    host_b = host.encode("idna")
    req = b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b + struct.pack("!H", port)
    s.sendall(req)
    hdr = s.recv(4)
    if len(hdr) != 4 or hdr[0] != 5 or hdr[1] != 0:
        s.close()
        raise OSError(f"SOCKS5 connect failed: {hdr!r}")
    atyp = hdr[3]
    if atyp == 1:
        s.recv(4 + 2)
    elif atyp == 3:
        ln = s.recv(1)[0]
        s.recv(ln + 2)
    elif atyp == 4:
        s.recv(16 + 2)
    else:
        s.close()
        raise OSError(f"SOCKS5 bad atyp={atyp}")
    return s


def pump(a: socket.socket, b: socket.socket) -> None:
    sockets = [a, b]
    try:
        while True:
            r, _, x = select.select(sockets, [], sockets, 300)
            if x or not r:
                break
            for src in r:
                dst = b if src is a else a
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def host_matches_bypass(host: str, patterns: list[str]) -> bool:
    """True if host matches a proxy_bypass PAC pattern."""
    h = (host or "").strip().lower().strip("[]").rstrip(".")
    if not h:
        return False
    for raw in patterns:
        p = raw.strip().lower()
        if not p:
            continue
        if "*" in p or "?" in p:
            if fnmatch.fnmatch(h, p):
                return True
        elif h == p or h.endswith("." + p):
            return True
    return False


def bypass_to_singbox(
    patterns: list[str],
) -> tuple[list[str], list[str]]:
    """Convert proxy_bypass patterns to sing-box domain_suffix / domain lists."""
    suffixes: set[str] = set()
    domains: set[str] = set()
    for raw in patterns:
        p = raw.strip().lower()
        if not p:
            continue
        if p.startswith("*."):
            suffixes.add(p[1:])
        elif "*" not in p and "?" not in p:
            domains.add(p)
        elif p.count("*") == 1 and p.startswith("*."):
            suffixes.add("." + p[2:])
    return sorted(suffixes), sorted(domains)


def _pac_cond(pattern: str) -> str:
    p = pattern.strip().lower()
    if not p:
        return ""
    if "*" in p or "?" in p:
        return f'shExpMatch(host, "{p}")'
    return f'(host == "{p}" || shExpMatch(host, "*.{p}"))'


def build_pac(
    listen_port: int,
    mode: str,
    tunnel_hosts: list[str],
    bypass_hosts: list[str],
    fallback: str,
    bypass_via: str,
) -> bytes:
    proxy = f"PROXY 127.0.0.1:{listen_port}"
    fb = f"PROXY {fallback}" if fallback else "DIRECT"
    if bypass_via == "corporate" and fallback:
        bypass_ret = f"PROXY {fallback}"
    else:
        bypass_ret = "DIRECT"

    bypass_conds = [_pac_cond(p) for p in bypass_hosts]
    bypass_conds = [c for c in bypass_conds if c]
    # Loopback / bare names / RFC1918 / link-local — never via VPS
    # (literal private IPs are also refused in the bridge itself)
    private_parts = [
        'host == "127.0.0.1"',
        'host == "localhost"',
        'host == "::1"',
        "isPlainHostName(host)",
        'shExpMatch(host, "10.*")',
        'shExpMatch(host, "192.168.*")',
        'shExpMatch(host, "169.254.*")',
        'dnsDomainIs(host, ".local")',
        'dnsDomainIs(host, ".internal")',
        'dnsDomainIs(host, ".localhost")',
        'shExpMatch(host, "metadata*")',
    ]
    # Explicit 172.16.0.0/12 (do not use 172.2*.* — it matches 172.2.x too)
    for n in range(16, 32):
        private_parts.append(f'shExpMatch(host, "172.{n}.*")')
    private = " ||\n        ".join(private_parts)
    bypass_body = private
    if bypass_conds:
        bypass_body += " ||\n        " + " ||\n        ".join(bypass_conds)

    if mode == "full":
        pac = f"""function FindProxyForURL(url, host) {{
    host = host.toLowerCase();
    if ({bypass_body})
        return "{bypass_ret}";
    return "{proxy}";
}}
"""
    else:
        tunnel_conds = [_pac_cond(p) for p in tunnel_hosts]
        tunnel_conds = [c for c in tunnel_conds if c]
        if not tunnel_conds:
            tunnel_conds = [
                'host == "github.com" || shExpMatch(host, "*.github.com")',
                'shExpMatch(host, "*.githubusercontent.com")',
                'shExpMatch(host, "*.githubassets.com")',
                'host == "cursor.com" || shExpMatch(host, "*.cursor.com")',
                'shExpMatch(host, "*.cursor.sh")',
                'shExpMatch(host, "*.cursor-cdn.com")',
                'shExpMatch(host, "*.cursorapi.com")',
                'shExpMatch(host, "*.cursorvm.com")',
            ]
        tunnel_body = " ||\n        ".join(tunnel_conds)
        pac = f"""function FindProxyForURL(url, host) {{
    host = host.toLowerCase();
    if ({bypass_body})
        return "{bypass_ret}";
    if ({tunnel_body})
        return "{proxy}";
    return "{fb}";
}}
"""
    return pac.encode("utf-8")


def _header_host(head: bytes) -> str:
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"host:"):
            return line.split(b":", 1)[1].strip().decode("ascii", "replace").lower()
    return ""


def _is_local_pac_request(method: str, target: str, head: bytes, listen_port: int) -> bool:
    """PAC only for requests aimed at the proxy itself — not GET http://site/."""
    if method != "GET":
        return False
    host = _header_host(head).split(":")[0]
    local_hosts = {"", "127.0.0.1", "localhost", "[::1]"}

    if target.startswith("http://") or target.startswith("https://"):
        u = urlsplit(target)
        path = u.path or "/"
        th = (u.hostname or "").lower()
        if th not in local_hosts and th != "127.0.0.1":
            return False
        return path in ("/proxy.pac", "/pac")

    path = target.split("?", 1)[0]
    if path in ("/proxy.pac", "/pac"):
        return True
    if path == "/" and host in local_hosts:
        return True
    if path == "/" and host.endswith(f":{listen_port}"):
        return True
    return False


def _refuse(client: socket.socket, code: int = 403, msg: str = "Forbidden") -> None:
    body = f"{msg}\n".encode("utf-8")
    client.sendall(
        f"HTTP/1.1 {code} {msg}\r\n"
        f"Content-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n".encode("ascii")
        + body
    )


def _direct_connect(host: str, port: int) -> socket.socket:
    s = socket.create_connection((host, port), timeout=30)
    _tune(s)
    return s


def _corporate_connect(proxy: str, host: str, port: int) -> socket.socket:
    phost, _, pport_s = proxy.replace("http://", "").replace("https://", "").partition(":")
    pport = int(pport_s or 3128)
    s = socket.create_connection((phost, pport), timeout=30)
    _tune(s)
    req = (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Proxy-Connection: keep-alive\r\n\r\n"
    )
    s.sendall(req.encode("ascii", "replace"))
    resp = b""
    while b"\r\n\r\n" not in resp and len(resp) < 8192:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    status = resp.split(b"\r\n", 1)[0].decode("ascii", "replace")
    if " 200 " not in status:
        s.close()
        raise OSError(f"corporate CONNECT failed: {status}")
    return s


def _connect_bypass(
    host: str,
    port: int,
    *,
    bypass_via: str,
    fallback_proxy: str,
) -> socket.socket:
    if bypass_via == "corporate" and fallback_proxy:
        return _corporate_connect(fallback_proxy, host, port)
    return _direct_connect(host, port)


def forward_http_absolute(
    client: socket.socket,
    head: bytes,
    rest: bytes,
    method: str,
    target: str,
    socks_host: str,
    socks_port: int,
    *,
    bypass_hosts: list[str] | None = None,
    fallback_proxy: str = "",
    bypass_via: str = "direct",
) -> None:
    u = urlsplit(target)
    host = u.hostname
    if not host:
        client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        return
    bypassed = host_matches_bypass(host, bypass_hosts or [])
    if not bypassed and is_blocked_destination(host):
        _refuse(client, 403, "Forbidden private/metadata destination")
        return
    port = u.port or (443 if u.scheme == "https" else 80)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query

    lines = head.split(b"\r\n")
    lines[0] = f"{method} {path} HTTP/1.1".encode("ascii", "replace")
    new_head = b"\r\n".join(lines) + b"\r\n\r\n"

    if bypassed:
        remote = _connect_bypass(
            host, port, bypass_via=bypass_via, fallback_proxy=fallback_proxy
        )
    else:
        remote = socks5_connect(socks_host, socks_port, host, port)
    remote.sendall(new_head + rest)
    client.settimeout(None)
    remote.settimeout(None)
    _tune(client)
    pump(client, remote)


def handle_client(
    client: socket.socket,
    socks_host: str,
    socks_port: int,
    pac_bytes: bytes,
    listen_port: int,
    *,
    bypass_hosts: list[str] | None = None,
    fallback_proxy: str = "",
    bypass_via: str = "direct",
) -> None:
    try:
        client.settimeout(60)
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                return
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
        parts = line.split()
        if len(parts) < 2:
            return
        method = parts[0].upper()
        target = parts[1]

        if method == "CONNECT":
            if ":" not in target:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            host, _, port_s = target.rpartition(":")
            host = host.strip("[]")
            try:
                port = int(port_s)
            except ValueError:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            bypassed = host_matches_bypass(host, bypass_hosts or [])
            if not bypassed and is_blocked_destination(host):
                _refuse(client, 403, "Forbidden private/metadata destination")
                return
            if bypassed:
                remote = _connect_bypass(
                    host, port, bypass_via=bypass_via, fallback_proxy=fallback_proxy
                )
            else:
                remote = socks5_connect(socks_host, socks_port, host, port)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(None)
            remote.settimeout(None)
            _tune(client)
            pump(client, remote)
            return

        if _is_local_pac_request(method, target, head, listen_port):
            client.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/x-ns-proxy-autoconfig\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(pac_bytes)}\r\n\r\n".encode("ascii")
                + pac_bytes
            )
            return

        path = target.split("?", 1)[0] if not target.startswith("http") else urlsplit(target).path
        if method == "GET" and path in ("/ok", "/health"):
            client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: close\r\n\r\nok\n")
            return

        if target.startswith("http://"):
            forward_http_absolute(
                client,
                head,
                rest,
                method,
                target,
                socks_host,
                socks_port,
                bypass_hosts=bypass_hosts,
                fallback_proxy=fallback_proxy,
                bypass_via=bypass_via,
            )
            return

        client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
    except OSError as exc:
        msg = str(exc)
        try:
            if "blocked" in msg:
                _refuse(client, 403, "Forbidden private/metadata destination")
            else:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
    except Exception:
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
    finally:
        try:
            client.close()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:1088")
    ap.add_argument("--socks", default="127.0.0.1:1080")
    ap.add_argument("--mode", choices=("full", "github"), default="full")
    ap.add_argument("--fallback-proxy", default="", help="e.g. 10.16.0.8:3128")
    ap.add_argument("--bypass-via", choices=("direct", "corporate"), default="direct")
    ap.add_argument("--pac-host", action="append", default=[], help="Tunnel these (github mode)")
    ap.add_argument("--bypass-host", action="append", default=[], help="Never send via VPS")
    args = ap.parse_args()

    lhost, _, lport_s = args.listen.partition(":")
    lport = int(lport_s)
    # Refuse non-loopback binds — open LAN proxy is never intended
    if lhost not in ("127.0.0.1", "::1", "localhost"):
        print(f"refusing non-loopback listen address: {lhost}", flush=True)
        return 2
    shost, _, sport_s = args.socks.partition(":")
    sport = int(sport_s)

    tunnel = list(args.pac_host)
    bypass = list(args.bypass_host)
    pac_bytes = build_pac(
        lport,
        args.mode,
        tunnel,
        bypass,
        args.fallback_proxy,
        args.bypass_via,
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lhost, lport))
    srv.listen(64)
    print(
        f"http-proxy={lhost}:{lport} mode={args.mode} socks5://{shost}:{sport} "
        f"pac=http://127.0.0.1:{lport}/proxy.pac bypass={len(bypass)} "
        f"ssrf_guard=on",
        flush=True,
    )

    while True:
        client, _ = srv.accept()
        # Only accept from loopback peers
        try:
            peer = client.getpeername()[0]
            if peer not in ("127.0.0.1", "::1") and not peer.startswith("127."):
                client.close()
                continue
        except OSError:
            try:
                client.close()
            except OSError:
                pass
            continue
        threading.Thread(
            target=handle_client,
            args=(client, shost, sport, pac_bytes, lport),
            kwargs={
                "bypass_hosts": bypass,
                "fallback_proxy": args.fallback_proxy,
                "bypass_via": args.bypass_via,
            },
            daemon=True,
        ).start()


if __name__ == "__main__":
    raise SystemExit(main())
