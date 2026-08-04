"""Background tunnel health monitor: detect dead SOCKS and auto-reconnect."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Callable

from desktop.client import OpsClient, _port_open
from desktop.config_io import (
    get_mode,
    get_tun_enabled,
    get_watchdog_enabled,
    get_watchdog_interval,
    get_watchdog_max_retries,
)
from desktop import procutil

LogFn = Callable[[str], None]
NotifyFn = Callable[[str, str], None]
SkipFn = Callable[[], bool]


def _noop(_msg: str) -> None:
    pass


def socks_port_from_client(client: OpsClient) -> int:
    try:
        cfg = client.config()
        return int((cfg.get("ssh") or {}).get("local_socks_port") or 1080)
    except Exception:  # noqa: BLE001
        return 1080


def health_problem(client: OpsClient) -> str | None:
    """Return a short reason if the tunnel looks broken, else None.

    Idle (nothing expected) is not a problem — caller decides via desired_on.
    """
    client.reload_env()
    mode = get_mode()
    if mode == "vps":
        # Relay mode has no local SOCKS; skip SOCKS checks.
        if client.paths.state_path.is_file():
            try:
                import json

                st = json.loads(client.paths.state_path.read_text(encoding="utf-8-sig"))
                if st.get("mode") == "relay":
                    return None
            except Exception:  # noqa: BLE001
                pass

    socks = socks_port_from_client(client)
    socks_up = _port_open("127.0.0.1", socks, timeout=0.35)

    ssh_pid = 0
    ssh_alive = False
    if client.paths.ssh_pid.is_file():
        try:
            ssh_pid = int(client.paths.ssh_pid.read_text().strip())
        except ValueError:
            ssh_pid = 0
        ssh_alive = bool(ssh_pid and procutil.pid_alive(ssh_pid))

    tun_up = client.tun.running()
    tun_wanted = get_tun_enabled()

    if tun_up and not socks_up:
        return f"TUN up but SOCKS :{socks} refused"
    if ssh_alive and not socks_up:
        return f"ssh pid={ssh_pid} alive but SOCKS :{socks} closed"
    if client.paths.state_path.is_file() and not socks_up and not ssh_alive:
        return f"state present but SOCKS :{socks} down"
    if ssh_pid and not ssh_alive and not socks_up:
        return f"ssh pid={ssh_pid} dead, SOCKS :{socks} down"
    # SOCKS alone is not enough for Docker Desktop: UDP/53 from the VM dies
    # unless sing-box hijacks it. Recover TUN when .env says TUN=1.
    if tun_wanted and socks_up and not tun_up:
        return "TUN=1 but sing-box down (Docker DNS)"
    return None


def infer_desired_on(client: OpsClient) -> bool:
    """True if leftover state suggests the user wanted the tunnel up."""
    if client.paths.state_path.is_file():
        return True
    if client.paths.ssh_pid.is_file():
        return True
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
        self._next_ok_at = 0.0
        self._reconnecting = False

    @property
    def desired(self) -> bool:
        return self._desired

    def set_desired(self, on: bool) -> None:
        self._desired = on
        if on:
            self._fail_streak = 0
            self._next_ok_at = 0.0
        else:
            self._fail_streak = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="ops-content-watchdog", daemon=True
        )
        self._thread.start()
        self.log("watchdog: started")

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    def tick(self) -> None:
        """One health check (also used by CLI watch loop)."""
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
        self.client.reload_env()
        if not get_watchdog_enabled():
            return
        if not self._desired:
            return
        now = time.monotonic()
        if now < self._next_ok_at:
            return

        problem = health_problem(self.client)
        if problem is None:
            if self._fail_streak:
                self.log("watchdog: tunnel healthy again")
            self._fail_streak = 0
            return

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
                # Stop TUN first so sing-box stops flooding refused-to-1080 logs
                try:
                    if self.client.tun.running():
                        self.client.tun.stop()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: TUN stop: {exc}")
                try:
                    self.client.stop_tunnel()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: stop: {exc}")
                self.client.enable(spawn_watchdog=False)
            problem = health_problem(self.client)
            if problem is None:
                self.log("watchdog: reconnected OK")
                self._notify(
                    "TUN восстановлен" if tun_only else "Туннель восстановлен",
                    "Docker DNS снова через host resolver"
                    if tun_only
                    else "SOCKS снова доступен",
                )
                self._fail_streak = 0
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
        if health_problem(client) is not None or not infer_desired_on(client):
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

