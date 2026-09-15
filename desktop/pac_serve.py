"""Tiny loopback HTTP server that only serves a PAC file (for MODE=singbox).

CLI: python -m desktop pac-serve --listen 127.0.0.1:1089 --proxy-port 1088 ...
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from desktop.logutil import noop  # noqa: E402
from lib.pac import build_pac  # noqa: E402

LogFn = Callable[[str], None]


def serve_pac_response(client: socket.socket, pac: bytes) -> None:
    try:
        client.settimeout(10)
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            chunk = client.recv(4096)
            if not chunk:
                return
            buf += chunk
        head = buf.split(b"\r\n\r\n", 1)[0]
        line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
        parts = line.split()
        path = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"
        if len(parts) >= 1 and parts[0].upper() == "GET" and path in (
            "/proxy.pac",
            "/pac",
            "/",
        ):
            client.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/x-ns-proxy-autoconfig\r\n"
                b"Cache-Control: max-age=600\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(pac)}\r\n\r\n".encode("ascii")
                + pac
            )
        else:
            client.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass


class PacServer:
    """Serve application/x-ns-proxy-autoconfig on 127.0.0.1 only."""

    def __init__(self) -> None:
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port: int | None = None
        self._pac: bytes = b""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        pac_bytes: bytes,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1089,
        log: LogFn = noop,
    ) -> int:
        self.stop()
        if listen_host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"refusing non-loopback PAC listen: {listen_host}")
        self._pac = pac_bytes
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((listen_host, listen_port))
        srv.listen(16)
        srv.settimeout(0.5)
        self._srv = srv
        self._stop.clear()
        self.port = listen_port

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    client, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
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
                    target=self._handle, args=(client,), daemon=True
                ).start()

        self._thread = threading.Thread(target=loop, name="ergoms-pac-serve", daemon=True)
        self._thread.start()
        log(f"PAC server http://127.0.0.1:{listen_port}/proxy.pac")
        return listen_port

    def replace_pac(self, pac_bytes: bytes) -> None:
        """Swap PAC body without closing :1089 — Chrome keeps the AutoConfigURL."""
        self._pac = pac_bytes

    def _handle(self, client: socket.socket) -> None:
        serve_pac_response(client, self._pac)

    def stop(self, log: LogFn = noop) -> None:
        self._stop.set()
        srv = self._srv
        self._srv = None
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        if self.port is not None:
            log(f"PAC server :{self.port} stopped")
        self.port = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PAC-only server for MODE=singbox")
    ap.add_argument("--listen", default="127.0.0.1:1089")
    ap.add_argument(
        "--proxy-port",
        type=int,
        default=1088,
        help="Port written into PAC PROXY line (sing-box HTTP inbound)",
    )
    ap.add_argument("--mode", choices=("full", "github"), default="full")
    ap.add_argument("--fallback-proxy", default="")
    ap.add_argument("--bypass-via", choices=("direct", "corporate"), default="direct")
    ap.add_argument("--pac-host", action="append", default=[])
    ap.add_argument("--bypass-host", action="append", default=[])
    ap.add_argument("--pid-file", default="")
    args = ap.parse_args(argv)

    lhost, _, lport_s = args.listen.partition(":")
    lport = int(lport_s or "1089")
    if lhost not in ("127.0.0.1", "::1", "localhost"):
        print(f"refusing non-loopback: {lhost}", flush=True)
        return 2

    pac = build_pac(
        int(args.proxy_port),
        args.mode,
        list(args.pac_host),
        list(args.bypass_host),
        args.fallback_proxy,
        args.bypass_via,
    )

    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()), encoding="utf-8")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lhost, lport))
    srv.listen(16)
    print(
        f"pac=http://127.0.0.1:{lport}/proxy.pac PROXY=127.0.0.1:{args.proxy_port} "
        f"mode={args.mode}",
        flush=True,
    )

    while True:
        client, _ = srv.accept()
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
            target=serve_pac_response, args=(client, pac), daemon=True
        ).start()


if __name__ == "__main__":
    raise SystemExit(main())
