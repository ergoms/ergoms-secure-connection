"""In-process HTTP→SOCKS bridge (stoppable), wrapping lib.http_via_socks."""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path
from typing import Callable

# Allow importing sibling lib/ whether frozen or from source
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.http_via_socks import build_pac, handle_client  # noqa: E402

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


class HttpBridge:
    def __init__(self) -> None:
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port: int | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 1088,
        socks_host: str = "127.0.0.1",
        socks_port: int = 1080,
        mode: str = "full",
        fallback_proxy: str = "",
        bypass_via: str = "direct",
        pac_hosts: list[str] | None = None,
        bypass_hosts: list[str] | None = None,
        log: LogFn = _noop,
    ) -> int:
        self.stop()
        if listen_host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"refusing non-loopback listen address: {listen_host}")

        tunnel = list(pac_hosts or [])
        bypass = list(bypass_hosts or [])
        pac_bytes = build_pac(
            listen_port, mode, tunnel, bypass, fallback_proxy, bypass_via
        )

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((listen_host, listen_port))
        srv.listen(64)
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
                    target=handle_client,
                    args=(client, socks_host, socks_port, pac_bytes, listen_port),
                    daemon=True,
                ).start()

        self._thread = threading.Thread(target=loop, name="ops-http-bridge", daemon=True)
        self._thread.start()
        log(
            f"HTTP bridge {listen_host}:{listen_port} mode={mode} "
            f"socks={socks_host}:{socks_port} bypass={len(bypass)}"
        )
        return listen_port

    def stop(self, log: LogFn = _noop) -> None:
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
            log("HTTP bridge stopped")
        self.port = None
