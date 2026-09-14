"""Background tunnel health monitor: detect dead SOCKS and auto-reconnect."""

from __future__ import annotations

import os
import socket
import ssl
import struct
import threading
import time
from datetime import datetime
from typing import Callable

from desktop.client import OpsClient, _port_open
from desktop.config_io import (
    get_local_socks_port,
    get_reverse_ssh_enabled,
    get_kill_switch,
    get_tun_enabled,
    get_watchdog_enabled,
    get_watchdog_interval,
    get_watchdog_max_retries,
)

LogFn = Callable[[str], None]
NotifyFn = Callable[[str, str], None]
SkipFn = Callable[[], bool]

# Public IP:443 — no DNS needed; not private (SSRF blocklist).
_PROBE_HOST = "1.1.1.1"
_PROBE_PORT = 443
_PROBE_TIMEOUT = 6.0
# Port-open is cheap; real CONNECT can flap once — require 2 fails.
_PROBE_FAILS_BEFORE_RECONNECT = 2


def _noop(_msg: str) -> None:
    pass


def socks_port_from_client(client: OpsClient) -> int:
    try:
        return get_local_socks_port(client.config())
    except Exception:  # noqa: BLE001
        return 1080


def socks_probe(
    socks_port: int,
    *,
    host: str = _PROBE_HOST,
    port: int = _PROBE_PORT,
    timeout: float = _PROBE_TIMEOUT,
) -> str | None:
    """SOCKS5 CONNECT probe. Return None if OK, else a short error string.

    Detects zombie tunnels: :1080 still accepts TCP but VLESS/Squid no longer
    forwards (the usual cause of TUN blackholing the whole OS).
    """
    s: socket.socket | None = None
    try:
        s = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        resp = s.recv(2)
        if len(resp) != 2 or resp[0] != 5 or resp[1] != 0:
            return f"SOCKS5 greeting failed: {resp!r}"
        try:
            ip_bytes = socket.inet_aton(host)
            req = b"\x05\x01\x00\x01" + ip_bytes + struct.pack("!H", port)
        except OSError:
            host_b = host.encode("ascii")
            req = (
                b"\x05\x01\x00\x03"
                + bytes([len(host_b)])
                + host_b
                + struct.pack("!H", port)
            )
        s.sendall(req)
        hdr = s.recv(4)
        if len(hdr) != 4:
            return "SOCKS5 CONNECT truncated"
        if hdr[0] != 5 or hdr[1] != 0:
            return f"SOCKS5 CONNECT rejected: rep={hdr[1]}"
        atyp = hdr[3]
        if atyp == 1:
            s.recv(4 + 2)
        elif atyp == 3:
            ln = s.recv(1)
            if not ln:
                return "SOCKS5 CONNECT bnd truncated"
            s.recv(ln[0] + 2)
        elif atyp == 4:
            s.recv(16 + 2)
        else:
            return f"SOCKS5 bad atyp={atyp}"
        return None
    except OSError as exc:
        return f"SOCKS probe: {exc}"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def socks_https_probe(
    socks_port: int,
    *,
    host: str = _PROBE_HOST,
    port: int = _PROBE_PORT,
    timeout: float = _PROBE_TIMEOUT,
    sni: str = "1.1.1.1",
    path: str = "/cdn-cgi/trace",
) -> str | None:
    """CONNECT + TLS + HTTP GET. CONNECT-only can pass while sites stay dead."""
    s: socket.socket | None = None
    try:
        s = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        resp = s.recv(2)
        if len(resp) != 2 or resp[0] != 5 or resp[1] != 0:
            return f"SOCKS5 greeting failed: {resp!r}"
        try:
            ip_bytes = socket.inet_aton(host)
            req = b"\x05\x01\x00\x01" + ip_bytes + struct.pack("!H", port)
        except OSError:
            host_b = host.encode("ascii")
            req = (
                b"\x05\x01\x00\x03"
                + bytes([len(host_b)])
                + host_b
                + struct.pack("!H", port)
            )
        s.sendall(req)
        hdr = s.recv(4)
        if len(hdr) != 4:
            return "SOCKS5 CONNECT truncated"
        if hdr[0] != 5 or hdr[1] != 0:
            return f"SOCKS5 CONNECT rejected: rep={hdr[1]}"
        atyp = hdr[3]
        if atyp == 1:
            s.recv(4 + 2)
        elif atyp == 3:
            ln = s.recv(1)
            if not ln:
                return "SOCKS5 CONNECT bnd truncated"
            s.recv(ln[0] + 2)
        elif atyp == 4:
            s.recv(16 + 2)
        else:
            return f"SOCKS5 bad atyp={atyp}"
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(s, server_hostname=sni)
        s = None
        host_hdr = host if ":" not in host else f"[{host}]"
        tls.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host_hdr}\r\nConnection: close\r\n\r\n".encode(
                "ascii"
            )
        )
        body = b""
        while True:
            chunk = tls.recv(4096)
            if not chunk:
                break
            body += chunk
            if len(body) > 8192:
                break
        tls.close()
        if b"HTTP/1." not in body[:32] and b"HTTP/2" not in body[:32]:
            return f"HTTPS empty/garbled: {body[:80]!r}"
        if body.startswith(b"HTTP/1.") and b" 200" not in body.split(b"\r\n", 1)[0]:
            status = body.split(b"\r\n", 1)[0].decode("ascii", "replace")
            return f"HTTPS {status}"
        return None
    except OSError as exc:
        return f"HTTPS probe: {exc}"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def health_problem(client: OpsClient, *, probe: bool = False) -> str | None:
    """Return a short reason if the tunnel looks broken, else None.

    Idle (nothing expected) is not a problem — caller decides via desired_on.
    Set probe=True for a real SOCKS5 CONNECT check (watchdog ticks).
    """
    client.reload_env()
    socks = socks_port_from_client(client)
    socks_up = _port_open("127.0.0.1", socks, timeout=0.35)

    singbox_alive = False
    try:
        singbox_alive = bool(client.singbox.running())
    except Exception:  # noqa: BLE001
        singbox_alive = False

    tun_up = client.tun.running() or (
        singbox_alive and getattr(client.singbox, "tun_active", lambda: False)()
    )
    tun_wanted = get_tun_enabled()

    if tun_up and not socks_up:
        return f"TUN up but SOCKS :{socks} refused"
    if singbox_alive and not socks_up:
        return f"singbox up but SOCKS :{socks} closed"
    if client.paths.state_path.is_file() and not socks_up and not singbox_alive:
        return f"state present but SOCKS :{socks} down"
    # Probe before TUN recovery — otherwise failsafe TUN-stop + "sing-box down"
    # would bring TUN back on top of a zombie SOCKS and blackhole the OS again.
    if probe and socks_up and (
        singbox_alive or tun_up or tun_wanted or client.paths.state_path.is_file()
    ):
        err = socks_https_probe(socks, timeout=10.0)
        if err:
            return f"SOCKS :{socks} zombie ({err})"
    # SOCKS alone is not enough for Docker Desktop: UDP/53 from the VM dies
    # unless sing-box hijacks it. Recover TUN when config says tun.enabled.
    if tun_wanted and socks_up and not tun_up:
        return "tun.enabled but sing-box down (Docker DNS)"
    return None


def infer_desired_on(client: OpsClient) -> bool:
    """True if leftover state suggests the user wanted the tunnel up."""
    if client.paths.state_path.is_file():
        return True
    try:
        if client.singbox.running():
            return True
    except Exception:  # noqa: BLE001
        pass
    if client.tun.running():
        return True
    return False


class TunnelWatchdog:
    """Poll tunnel health and call enable() when SOCKS dies unexpectedly."""

    def __init__(
        self,
        client: OpsClient,
        *,
        log: LogFn = _noop,
        on_notify: NotifyFn | None = None,
        should_skip: SkipFn | None = None,
    ) -> None:
        self.client = client
        self.log = log
        self.on_notify = on_notify
        self.should_skip = should_skip
        self._desired = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._fail_streak = 0
        self._probe_fails = 0
        self._next_ok_at = 0.0
        self._reconnecting = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def desired(self) -> bool:
        return self._desired

    def set_desired(self, on: bool) -> None:
        self._desired = on
        if on:
            self._fail_streak = 0
            self._probe_fails = 0
            self._next_ok_at = 0.0
        else:
            self._fail_streak = 0
            self._probe_fails = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ergoms-secure-connection-watchdog", daemon=True
        )
        self._thread.start()
        self.log("watchdog: started")

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=0.3)
        self._thread = None

    def tick(self) -> None:
        """One health check (also used by CLI watch loop)."""
        if self._stop.is_set():
            return
        if self.should_skip and self.should_skip():
            return
        if self._reconnecting:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._tick_body()
        finally:
            self._lock.release()

    def _tick_body(self) -> None:
        if self._stop.is_set() or not self._desired:
            return
        self.client.reload_env()
        if not get_watchdog_enabled():
            return
        now = time.monotonic()
        if now < self._next_ok_at:
            return
        if self._stop.is_set():
            return

        # Always probe SOCKS CONNECT when the port is up — port-open alone
        # misses zombie VLESS/Squid (TUN still up → whole OS blackholed).
        problem = health_problem(self.client, probe=True)
        if problem and "zombie" in problem:
            self._probe_fails += 1
            self.log(
                f"watchdog: {problem} "
                f"({self._probe_fails}/{_PROBE_FAILS_BEFORE_RECONNECT})"
            )
            if get_kill_switch():
                try:
                    self.client._ensure_kill_switch(self.client.config())  # noqa: SLF001
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: kill switch: {exc}")
            else:
                try:
                    if self.client.singbox.running():
                        self.client.singbox.stop()
                        self.log("watchdog: singbox stopped (failsafe while SOCKS zombie)")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: singbox failsafe stop: {exc}")
            if self._probe_fails < _PROBE_FAILS_BEFORE_RECONNECT:
                return
        elif problem is None:
            if self._fail_streak or self._probe_fails:
                self.log("watchdog: tunnel healthy again")
            self._fail_streak = 0
            self._probe_fails = 0
            self._ensure_reverse_ssh()
            return
        else:
            # Hard failure (port closed / process dead) — reset soft probe counter
            self._probe_fails = 0

        max_retries = get_watchdog_max_retries()
        if self._fail_streak >= max_retries:
            # Back off hard — notify once per cooldown window
            self._next_ok_at = now + max(60, get_watchdog_interval() * 6)
            self.log(
                f"watchdog: still broken ({problem}); "
                f"paused after {max_retries} retries, next try later"
            )
            return

        self._fail_streak += 1
        self.log(f"watchdog: {problem} — reconnect {self._fail_streak}/{max_retries}")
        if "sing-box down" in problem:
            self._notify("TUN упал", f"{problem}. Поднимаю…")
            self._reconnect(tun_only=True)
        else:
            self._notify("Туннель упал", f"{problem}. Переподключаю…")
            self._reconnect(tun_only=False)

    def _reconnect(self, *, tun_only: bool = False) -> None:
        self._reconnecting = True
        try:
            if tun_only:
                try:
                    self.client.enable_tun(persist=False)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: TUN restart: {exc}")
                    raise
            else:
                try:
                    self.client.stop_singbox_mode()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: stop: {exc}")
                self.client.enable(spawn_watchdog=False)
            problem = health_problem(self.client, probe=True)
            if problem is None:
                self.log("watchdog: reconnected OK")
                self._notify(
                    "TUN восстановлен" if tun_only else "Туннель восстановлен",
                    "Docker DNS снова через host resolver"
                    if tun_only
                    else "SOCKS снова доступен",
                )
                self._fail_streak = 0
                self._probe_fails = 0
                self._next_ok_at = time.monotonic() + 5.0
            else:
                delay = min(120, get_watchdog_interval() * (2 ** min(self._fail_streak, 4)))
                self._next_ok_at = time.monotonic() + delay
                self.log(f"watchdog: still unhealthy ({problem}), retry in {int(delay)}s")
                self._notify("Переподключение не удалось", problem)
        except Exception as exc:  # noqa: BLE001
            delay = min(120, get_watchdog_interval() * (2 ** min(self._fail_streak, 4)))
            self._next_ok_at = time.monotonic() + delay
            self.log(f"watchdog: reconnect failed: {exc}; retry in {int(delay)}s")
            self._notify("Ошибка переподключения", str(exc)[:120])
        finally:
            self._reconnecting = False

    def _ensure_reverse_ssh(self) -> None:
        if not get_reverse_ssh_enabled():
            return
        try:
            if self.client.reverse_ssh.running():
                return
        except Exception:  # noqa: BLE001
            return
        self.log("watchdog: reverse-ssh down — поднимаю")
        try:
            self.client._maybe_start_reverse_ssh()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            self.log(f"watchdog: reverse-ssh: {exc}")

    def _notify(self, title: str, message: str) -> None:
        if self.on_notify:
            try:
                self.on_notify(title, message)
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        # Stagger first check so GUI can finish enable()
        if self._stop.wait(3.0):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                self.log(f"watchdog: tick error: {exc}")
            interval = max(5, get_watchdog_interval())
            if self._stop.wait(interval):
                break


def _daemon_logger(client: OpsClient) -> LogFn:
    log_path = client.paths.logs_dir / "watchdog.log"
    client.paths.logs_dir.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} {msg}\n"
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    return log


def run_watch_forever(
    client: OpsClient,
    *,
    log: LogFn | None = None,
    daemon: bool = False,
) -> int:
    """CLI entry: keep tunnel up until Ctrl+C (or forever as --daemon child)."""
    if daemon:
        log = _daemon_logger(client)
        # Refresh pid file (parent already wrote Popen pid; keep in sync)
        try:
            client.paths.watchdog_pid.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        log(f"watchdog daemon start pid={os.getpid()} interval={get_watchdog_interval()}s")
    else:
        log = log or client.log

    wd = TunnelWatchdog(client, log=log)
    wd.set_desired(True)

    if not daemon:
        if health_problem(client, probe=True) is not None or not infer_desired_on(client):
            log("watch: ensuring tunnel is up…")
            try:
                client.enable(spawn_watchdog=False)
            except Exception as exc:  # noqa: BLE001
                log(f"watch: initial enable failed: {exc}")
        wd.set_desired(True)
        log(
            f"watch: monitoring every {get_watchdog_interval()}s "
            f"(Ctrl+C to stop watchdog; tunnel stays up)"
        )
    try:
        while True:
            wd.tick()
            time.sleep(max(5, get_watchdog_interval()))
    except KeyboardInterrupt:
        log("watch: stopped")
        return 0

