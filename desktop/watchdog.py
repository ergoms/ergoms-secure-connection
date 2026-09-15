"""Background tunnel health monitor: detect dead SOCKS and auto-reconnect."""

from __future__ import annotations

import os
import ssl
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Protocol

from desktop.config_io import (
    get_local_socks_port,
    get_reverse_ssh_enabled,
    get_kill_switch,
    get_tun_enabled,
    get_watchdog_enabled,
    get_watchdog_interval,
)
from desktop.logutil import noop
from lib.netutil import port_open
from lib.socks5 import connect as socks5_connect

LogFn = Callable[[str], None]
NotifyFn = Callable[[str, str], None]
SkipFn = Callable[[], bool]


class WatchHost(Protocol):
    def reload_env(self) -> None: ...
    def config(self) -> dict[str, Any]: ...
    def enable(self, *, spawn_watchdog: bool = True) -> None: ...
    def enable_tun(self, *, persist: bool = True) -> None: ...
    def stop_singbox_mode(self) -> None: ...
    def _ensure_kill_switch(self, cfg: dict[str, Any]) -> list[str]: ...
    def _maybe_start_reverse_ssh(self, cfg: dict[str, Any] | None = None) -> None: ...

    log: Callable[[str], None]
    paths: Any
    singbox: Any
    tun: Any
    reverse_ssh: Any

# Liveness only. github/openai 403 their bots — that is not a dead tunnel.
_PROBE_HOST = "1.1.1.1"
_PROBE_PORT = 443
_PROBE_TIMEOUT = 8.0


def exit_probe_target(*, office: bool) -> tuple[str, str]:
    """Stable HTTPS check (Cloudflare trace). Site 403 must not seal the KS."""
    del office
    return "1.1.1.1", "/cdn-cgi/trace"
# Port-open is cheap; real CONNECT can flap once — require 2 fails.
_PROBE_FAILS_BEFORE_RECONNECT = 2


def socks_port_from_client(client: WatchHost) -> int:
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
    s = None
    try:
        s = socks5_connect(
            "127.0.0.1",
            socks_port,
            host,
            port,
            timeout=timeout,
            prefer_ipv4_atyp=True,
            encoding="ascii",
        )
        return None
    except OSError as exc:
        msg = str(exc)
        if msg.startswith("SOCKS5"):
            if "bind truncated" in msg:
                return "SOCKS5 CONNECT bnd truncated"
            return msg
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
    sni: str = "",
    path: str = "/cdn-cgi/trace",
) -> str | None:
    """CONNECT + TLS + HTTP GET. CONNECT-only can pass while sites stay dead."""
    s = None
    try:
        s = socks5_connect(
            "127.0.0.1",
            socks_port,
            host,
            port,
            timeout=timeout,
            prefer_ipv4_atyp=True,
            encoding="ascii",
        )
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(s, server_hostname=sni or host)
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
        if body.startswith(b"HTTP/1."):
            status = body.split(b"\r\n", 1)[0]
            parts = status.split()
            try:
                code = int(parts[1]) if len(parts) >= 2 else 0
            except ValueError:
                code = 0
            # 403/404/429 = path is live (WAF). Only 5xx looks like a dead hop.
            if code < 100 or code >= 500:
                return f"HTTPS {status.decode('ascii', 'replace')}"
        return None
    except OSError as exc:
        msg = str(exc)
        if msg.startswith("SOCKS5"):
            if "bind truncated" in msg:
                return "SOCKS5 CONNECT bnd truncated"
            return msg
        return f"HTTPS probe: {exc}"
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def health_problem(client: WatchHost, *, probe: bool = False) -> str | None:
    """Return a short reason if the tunnel looks broken, else None.

    Idle (nothing expected) is not a problem — caller decides via desired_on.
    Set probe=True for a real SOCKS5 CONNECT check (watchdog ticks).
    """
    client.reload_env()
    socks = socks_port_from_client(client)
    socks_up = port_open("127.0.0.1", socks, timeout=0.35)

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
        office = False
        try:
            from desktop.config_io import resolve_corporate_proxy

            office = bool(resolve_corporate_proxy(client.config()))
        except Exception:  # noqa: BLE001
            office = False
        host, path = exit_probe_target(office=office)
        err = socks_https_probe(socks, host=host, sni=host, path=path, timeout=10.0)
        if err:
            return f"SOCKS :{socks} zombie ({err})"
    # SOCKS alone is not enough for Docker Desktop: UDP/53 from the VM dies
    # unless sing-box hijacks it. Recover TUN when config says tun.enabled.
    if tun_wanted and socks_up and not tun_up:
        return "tun.enabled but sing-box down (Docker DNS)"
    return None


def infer_desired_on(client: WatchHost) -> bool:
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
        client: WatchHost,
        *,
        log: LogFn = noop,
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
            self._ensure_tun_default()
            return
        else:
            # Hard failure (port closed / process dead) — reset soft probe counter
            self._probe_fails = 0

        self._fail_streak += 1
        self.log(f"watchdog: {problem} — переподключение #{self._fail_streak}")
        if "sing-box down" in problem:
            if self._fail_streak <= 3 or self._fail_streak % 10 == 0:
                self._notify("TUN упал", f"{problem}. Поднимаю…")
            self._reconnect(tun_only=True)
        else:
            if self._fail_streak <= 3 or self._fail_streak % 10 == 0:
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
                if self._fail_streak <= 3 or self._fail_streak % 10 == 0:
                    self._notify("Переподключение не удалось", problem)
        except Exception as exc:  # noqa: BLE001
            delay = min(120, get_watchdog_interval() * (2 ** min(self._fail_streak, 4)))
            self._next_ok_at = time.monotonic() + delay
            self.log(f"watchdog: reconnect failed: {exc}; retry in {int(delay)}s")
            if self._fail_streak <= 3 or self._fail_streak % 10 == 0:
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

    def _ensure_tun_default(self) -> None:
        if sys.platform != "win32":
            return
        if not (get_tun_enabled() or get_kill_switch()):
            return
        from desktop.config_io import get_server_host, resolve_corporate_proxy
        from desktop.kill_switch import allow_ips, apply as apply_kill_switch
        from desktop.tun import reclaim_tun_default, tun_owns_default

        if tun_owns_default():
            return
        self.log("watchdog: TUN не владеет default — снимаю чужой /1")
        if reclaim_tun_default() and tun_owns_default():
            self.log("watchdog: TUN снова владеет default")
            return
        self.log("watchdog: чужой VPN перекрыл TUN")
        self._notify(
            "Чужой VPN",
            "Второй VPN перекрыл маршруты. Отключите его.",
        )
        try:
            cfg = self.client.config()
            hosts = [get_server_host(cfg)]
            proxy = resolve_corporate_proxy(cfg)
            if proxy:
                hosts.append(proxy.split(":")[0])
            apply_kill_switch(
                allow_ips(*hosts),
                var_dir=self.client.paths.var_dir,
                log=self.log,
                blackhole=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"watchdog: fail-closed: {exc}")

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


def _daemon_logger(client: WatchHost) -> LogFn:
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
    client: WatchHost,
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

