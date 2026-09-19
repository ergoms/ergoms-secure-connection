"""Core Windows client: tunnel / status / probe / test."""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from desktop import procutil
from desktop.client_util import find_pythonw, pid_from_file, which
from desktop.config_io import (
    apply_config,
    get_http_bridge_port,
    get_kill_switch,
    get_local_socks_port,
    get_pac_listen_port,
    get_reverse_ssh,
    get_reverse_ssh_enabled,
    get_server,
    get_server_host,
    get_sing_box_path,
    get_socks_scope,
    get_tun_elevate,
    get_tun_enabled,
    get_watchdog_enabled,
    invoke_init,
    load_config,
    resolve_corporate_proxy,
    update_config_key,
)
from desktop.connection import ConnectionOps
from desktop.git_proxy import git_get
from desktop.integrations import IntegrationOps
from desktop.kill_switch import is_applied as kill_switch_is_applied
from desktop.kill_switch import state_path as kill_switch_state_path
from desktop.lifecycle.pipeline import ConnectionPipeline
from desktop.lifecycle.session import ConnectionSession
from desktop.logutil import noop
from desktop.paths import Paths, is_frozen, self_command
from desktop.probes import ProbeOps
from desktop.reverse_ssh import ReverseSshManager
from desktop.singbox_mode import (
    SingboxModeManager,
    amneziawg_opts,
    choose_dial,
)
from desktop.tun import TunManager
from lib.netutil import port_open

LogFn = Callable[[str], None]


class OpsClient(ConnectionOps, ProbeOps, IntegrationOps):
    def __init__(
        self,
        paths: Paths | None = None,
        log: LogFn = noop,
        *,
        startup_cleanup: bool = True,
        inprocess_helpers: bool = False,
    ) -> None:
        self.paths = paths or Paths()
        self._user_log = log
        self.log = self._log
        self._inprocess_helpers = inprocess_helpers
        self._pac_server: Any = None
        self._watchdog: Any = None
        self.paths.ensure_dirs()
        if self.paths.config_path.is_file():
            apply_config(self.paths.config_path, env_path=self.paths.env_path)
        self.tun = TunManager(
            self.paths.var_dir,
            self.paths.tools_dir,
            self.paths.logs_dir,
            log=self.log,
        )
        self.singbox = SingboxModeManager(
            self.paths.var_dir,
            self.paths.tools_dir,
            self.paths.logs_dir,
            log=self.log,
        )
        self.reverse_ssh = ReverseSshManager(self.paths, log=self.log)
        self.session = ConnectionSession(log=self.log)
        self.pipeline = ConnectionPipeline(self)
        self._atexit_done = False
        self._teardown_lock = threading.Lock()
        atexit.register(self._atexit_teardown)
        if startup_cleanup:
            try:
                if not self._vpn_process_up():
                    self.teardown_overrides_if_dirty()
            except Exception:  # noqa: BLE001
                pass

    def _log(self, msg: str) -> None:
        self._user_log(msg)
        try:
            self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with (self.paths.logs_dir / "ergoms-secure-connection.log").open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {msg}\n")
        except OSError:
            pass

    def _log_singbox_tail(self, reason: str) -> None:
        lines = self.singbox.tail_log(24)
        if not lines:
            self.log(f"журнал sing-box пуст ({self.singbox.log_path})")
            return
        if reason != "после запуска" and any(
            "dial tcp" in ln and ":443: i/o timeout" in ln for ln in lines
        ):
            self.log(
                "VPS :443 не отвечает — проверьте доступность сервера/DPI "
                f"({reason})"
            )
            return
        self.log(f"журнал sing-box ({reason}):")
        for ln in lines:
            self.log(f"  {ln}")

    def reload_env(self) -> None:
        if self.paths.config_path.is_file():
            apply_config(
                self.paths.config_path,
                force=True,
                env_path=self.paths.env_path,
            )

    def init(self) -> None:
        invoke_init(self.paths, log=self.log)

    def config(self) -> dict[str, Any]:
        return load_config(self.paths.config_path)


    def download_sing_box(self) -> None:
        self.reload_env()
        proxy_url = None
        http_port = get_http_bridge_port()
        if port_open("127.0.0.1", http_port):
            proxy_url = f"http://127.0.0.1:{http_port}"
        path = self.tun.ensure_downloaded(proxy_url=proxy_url)
        # Keep empty in config → auto-resolve tools/sing-box next to project
        if self.paths.config_path.is_file():
            cfg = self.config()
            cfg.setdefault("tun", {})
            cfg["tun"]["sing_box_path"] = ""
            from desktop.config_io import save_config

            save_config(self.paths.config_path, cfg)
        self.log(f"sing-box ready (auto): {path}")

    def download_awg_sing_box(self) -> None:
        self.reload_env()
        proxy_url = None
        http_port = get_http_bridge_port()
        if port_open("127.0.0.1", http_port):
            proxy_url = f"http://127.0.0.1:{http_port}"
        path = self.tun.ensure_awg_downloaded(proxy_url=proxy_url)
        self.log(f"sing-box AWG ready: {path}")

    def watchdog_daemon_alive(self) -> bool:
        if self._watchdog is not None and getattr(self._watchdog, "running", False):
            return True
        if self._inprocess_helpers:
            return False
        if not self.paths.watchdog_pid.is_file():
            return False
        try:
            pid = int(self.paths.watchdog_pid.read_text().strip())
        except ValueError:
            return False
        return bool(pid and procutil.pid_alive(pid))

    def _stop_watchdog_inprocess(self) -> None:
        wd = self._watchdog
        if wd is None:
            return
        try:
            wd.set_desired(False)
            wd.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"watchdog stop: {exc}")
        self._watchdog = None

    def _ensure_watchdog_inprocess(self) -> None:
        if not get_watchdog_enabled():
            self._stop_watchdog_inprocess()
            self.log("WATCHDOG=0 — фоновый сторож не запускаем")
            return
        from desktop.watchdog import TunnelWatchdog

        if self._watchdog is None:
            self._watchdog = TunnelWatchdog(
                self,
                log=self.log,
                should_skip=lambda: bool(
                    self.session.in_operation
                    or self.session.snapshot.phase.holds_watchdog
                ),
            )
        self._watchdog.set_desired(True)
        self._watchdog.start()

    def stop_watchdog_daemon(self) -> None:
        self._stop_watchdog_inprocess()
        targets: list[int] = []
        pid = pid_from_file(self.paths.watchdog_pid, unlink=True)
        if pid and pid != os.getpid():
            targets.append(pid)
        for extra in procutil.pids_cmdline_match_many(
            ("watch --daemon", "-m desktop watch"), cache=False
        ).values():
            targets.extend(extra)
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for dead in killed:
            self.log(f"watchdog pid={dead} остановлен")
        leftover = [p for p in targets if p != os.getpid() and procutil.pid_alive(p)]
        if leftover:
            self.log(
                f"watchdog всё ещё жив pid={','.join(str(p) for p in leftover)} "
                "— он может снова поднять sing-box"
            )

    def ensure_watchdog_daemon(self) -> None:
        """Spawn background `watch --daemon` so CLI `on` keeps monitoring after exit."""
        self.reload_env()
        if self._inprocess_helpers:
            self._ensure_watchdog_inprocess()
            return
        if not get_watchdog_enabled():
            self.log("WATCHDOG=0 — фоновый сторож не запускаем")
            return
        if self.watchdog_daemon_alive():
            try:
                pid = int(self.paths.watchdog_pid.read_text().strip())
            except ValueError:
                pid = 0
            self.log(f"watchdog already running (pid={pid})")
            return

        args = [*self_command(), "watch", "--daemon"]
        if not is_frozen():
            pyw = find_pythonw()
            if pyw and Path(args[0]).name.lower().startswith("python"):
                args[0] = pyw

        env = os.environ.copy()
        root = str(self.paths.root)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"
        env["ERGOMS_SC_WATCHDOG_CHILD"] = "1"

        proc = procutil.popen(args, env=env, cwd=root, detached=True)
        self.paths.watchdog_pid.write_text(str(proc.pid), encoding="utf-8")
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.paths.watchdog_pid.unlink(missing_ok=True)
                self.log(
                    f"watchdog exited immediately (code={proc.returncode}); "
                    "see logs/watchdog.log"
                )
                return
            time.sleep(0.05)
        self.log(f"watchdog pid={proc.pid} (logs/watchdog.log)")

    def _maybe_start_reverse_ssh(self, cfg: dict[str, Any] | None = None) -> None:
        if cfg is None:
            cfg = self.config()
        if not get_reverse_ssh_enabled(cfg):
            if self.reverse_ssh.running():
                self.reverse_ssh.stop()
            return
        try:
            self.reverse_ssh.start(cfg)
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh: {exc}")
            self.log("Туннель оставлен. Повторите reverse-on после ключа/sshd.")

    def enable_reverse_ssh(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist:
            update_config_key(self.paths.config_path, "reverse_ssh.enabled", True)
            self.log("reverse_ssh.enabled=true в config.json")
        cfg = self.config()
        if not self.singbox.running() and not port_open(
            "127.0.0.1", get_local_socks_port(cfg)
        ):
            raise RuntimeError("Сначала включите туннель: ergoms-secure-connection on")
        self.reverse_ssh.start(cfg)

    def disable_reverse_ssh(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist:
            update_config_key(self.paths.config_path, "reverse_ssh.enabled", False)
            self.log("reverse_ssh.enabled=false в config.json")
        self.reverse_ssh.stop()


    def needs_elevation(self, *, action: str = "on") -> bool:
        """True if this action would otherwise pop multiple UAC/cmd prompts."""
        if procutil.is_admin():
            return False
        self.reload_env()
        tun = get_tun_enabled() or get_kill_switch()
        elevate = get_tun_elevate()
        if action in ("on", "tun-on"):
            return bool((tun and elevate) or get_kill_switch())
        if kill_switch_is_applied():
            return True
        if (tun and elevate) and self.singbox.running():
            return True
        return False


    def status(self, *, include_git: bool = True) -> dict[str, Any]:
        """Snapshot of tunnel state.

        include_git=False skips spawning `git config` (faster for GUI polling).
        """
        self.reload_env()
        info: dict[str, Any] = {
            "mode": "singbox",
            "socks_scope": get_socks_scope(),
            "git_http_proxy": "",
            "git_https_proxy": "",
            "singbox_running": False,
            "singbox_pid": None,
            "http_port": get_http_bridge_port(),
            "pac_port": get_pac_listen_port(),
            "corporate_proxy": "",
            "server_target": "",
            "ssh_target": "",
            "proxy_bypass_n": 0,
            "state": None,
            "lines": [],
        }
        lines = info["lines"]
        info["tun_env"] = get_tun_enabled()
        lines.append("mode             = vless-reality")
        lines.append(f"SOCKS_SCOPE       = {info['socks_scope']}")
        lines.append(f"tun.enabled       = {1 if info['tun_env'] else 0}")
        info["kill_switch"] = get_kill_switch()
        ks_file = kill_switch_state_path(self.paths.var_dir).is_file()
        info["kill_switch_applied"] = (
            kill_switch_is_applied() if info["kill_switch"] and ks_file else False
        )
        lines.append(
            f"kill_switch       = {1 if info['kill_switch'] else 0}"
            + (" applied" if info["kill_switch_applied"] else "")
        )
        snap = self.session.snapshot
        info["exit_probe_error"] = snap.exit_probe_error
        info["exit_probe_hint"] = snap.exit_probe_hint
        info["fail_closed"] = bool(snap.fail_closed)
        info["phase"] = snap.phase.value
        if info["exit_probe_error"]:
            lines.append(f"exit_probe       = FAIL {info['exit_probe_error']}")
        if include_git:
            info["git_http_proxy"] = git_get("http.proxy")
            info["git_https_proxy"] = git_get("https.proxy")
            lines.append(f"git http.proxy  = {info['git_http_proxy']}")
            lines.append(f"git https.proxy = {info['git_https_proxy']}")

        self._status_fill_ports(info, lines)

        # TUN inbound inside sing-box. tun_running is "we want TUN and process lives";
        # tun_ready is the adapter actually UP — otherwise UI says «Защищено» too early.
        info["tun_wanted"] = bool(get_tun_enabled() or get_kill_switch())
        info["tun_running"] = bool(info["singbox_running"] and info["tun_wanted"])
        info["tun_ready"] = False
        if info["tun_running"]:
            if sys.platform == "win32":
                from desktop.tun import wait_tun_iface

                info["tun_ready"] = bool(wait_tun_iface(timeout=0.05))
            else:
                info["tun_ready"] = True
        info["connecting"] = bool(self.session.snapshot.connecting)
        info["tun_pid"] = (
            info["singbox_pid"] if info["tun_running"] and info["singbox_running"] else None
        )
        if info["tun_running"]:
            lines.append(f"TUN (sing-box) pid={info['tun_pid']} running")
        else:
            lines.append("TUN (sing-box)   = off")
            if info["tun_env"]:
                lines.append(
                    "WARN: TUN=1 but sing-box off — DNS в Docker Desktop сломан "
                    "(tun-on или ergoms-secure-connection on)"
                )

        if self.paths.state_path.is_file():
            try:
                info["state"] = json.loads(self.paths.state_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                info["state"] = None

        info["watchdog_running"] = self.watchdog_daemon_alive()
        info["watchdog_pid"] = None
        if self.paths.watchdog_pid.is_file():
            try:
                info["watchdog_pid"] = int(self.paths.watchdog_pid.read_text().strip())
            except ValueError:
                info["watchdog_pid"] = None
        if info["watchdog_running"]:
            lines.append(f"watchdog pid={info['watchdog_pid']} running")
        elif get_watchdog_enabled() and (
            info.get("singbox_running")
            or info.get("tun_running")
            or info.get("state")
        ):
            lines.append("watchdog         = off")

        rev = {}
        try:
            rev = get_reverse_ssh(self.config()) if self.paths.config_path.is_file() else {}
        except Exception:  # noqa: BLE001
            rev = {}
        info["reverse_ssh_enabled"] = bool(rev.get("enabled"))
        info["reverse_ssh_listen"] = int(rev.get("listen_port") or 2222)
        info["reverse_ssh_running"] = self.reverse_ssh.running()
        info["reverse_ssh_pid"] = self.reverse_ssh.pid()
        if info["reverse_ssh_running"]:
            lines.append(
                f"reverse-ssh pid={info['reverse_ssh_pid']} "
                f"VPS:127.0.0.1:{info['reverse_ssh_listen']} → клиент:{rev.get('local_port') or 22}"
            )
            hint = self.paths.var_dir / "reverse-ssh.txt"
            if hint.is_file():
                first = next(
                    (
                        ln.strip()
                        for ln in hint.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.startswith("#")
                    ),
                    "",
                )
                if first:
                    lines.append(f"с VPS           = {first}")
        elif info["reverse_ssh_enabled"] and (
            info.get("singbox_running") or info.get("state")
        ):
            lines.append("reverse-ssh      = down (reverse-on)")

        if info.get("state"):
            lines.append(f"state: {json.dumps(info['state'])}")
        elif self.paths.state_path.is_file() and info.get("state") is None:
            lines.append("state: (invalid)")

        if self.paths.config_path.is_file():
            self._status_fill_config(info, lines)

        if self.paths.docker_env.is_file():
            lines.append(f"docker.env      = {self.paths.docker_env}")
        if self.paths.docker_compose_proxy.is_file():
            lines.append(f"docker compose  = {self.paths.docker_compose_proxy}")

        info["active"] = bool(info.get("singbox_running") or info.get("tun_running"))
        return info

    def _status_fill_ports(self, info: dict[str, Any], lines: list[str]) -> None:
        info["singbox_running"] = self.singbox.running()
        info["singbox_pid"] = self.singbox.pid()
        try:
            socks_port = get_local_socks_port()
        except Exception:  # noqa: BLE001
            socks_port = 1080
        info["socks_port"] = socks_port
        listening = procutil.pids_listening_on_many(
            [socks_port, int(info["http_port"]), int(info["pac_port"])]
        )
        info["socks_up"] = bool(listening.get(socks_port))
        info["http_up"] = bool(listening.get(int(info["http_port"])))
        info["pac_up"] = bool(listening.get(int(info["pac_port"])))
        if not info["singbox_running"]:
            return
        lines.append(f"singbox mode pid={info['singbox_pid']} running")
        if info["socks_up"]:
            lines.append(f"singbox SOCKS    = 127.0.0.1:{socks_port}")
        else:
            lines.append(f"WARN: процесс есть, SOCKS :{socks_port} не слушает")
        if info["http_up"]:
            lines.append(f"singbox HTTP     = 127.0.0.1:{info['http_port']}")
        else:
            lines.append(f"WARN: HTTP :{info['http_port']} не слушает")
        if info["pac_up"]:
            lines.append(f"PAC             = http://127.0.0.1:{info['pac_port']}/proxy.pac")

    def _status_fill_config(self, info: dict[str, Any], lines: list[str]) -> None:
        cfg = self.config()
        info["corporate_proxy"] = resolve_corporate_proxy(cfg)
        host = get_server_host(cfg)
        port = int(get_server(cfg).get("port") or 443)
        info["server_target"] = f"{host}:{port}"
        info["ssh_target"] = info["server_target"]
        info["proxy_bypass_n"] = len(cfg.get("proxy_bypass") or [])
        info["sing_box_path"] = get_sing_box_path(cfg)
        tr = cfg.get("transport") or {}
        info["transport_type"] = str(tr.get("type") or "")
        lines.append(f"corporate_proxy = {info['corporate_proxy']}")
        lines.append(f"server          = {info['server_target']}")
        uuid = str(tr.get("uuid") or "")
        uuid_show = (uuid[:8] + "…") if len(uuid) > 8 else (uuid or "(empty)")
        awg = amneziawg_opts(tr) if isinstance(tr, dict) else None
        office = bool(info["corporate_proxy"])
        dial = choose_dial(tr if isinstance(tr, dict) else {}, office=office)
        lines.append(
            f"transport       = {info['transport_type'] or 'vless-reality'} "
            f"uuid={uuid_show} sni={tr.get('server_name') or ''} "
            f"dial={dial}"
        )
        if awg:
            lines.append(
                f"amneziawg       = udp :{awg['port']} {awg.get('address') or ''} "
                f"(дом={'вкл' if dial == 'amneziawg' else 'не выбран'})"
            )
        lines.append(f"proxy_bypass    = {info['proxy_bypass_n']} entries")
        if info["sing_box_path"]:
            lines.append(f"sing_box_path   = {info['sing_box_path']}")

    def test_bypass(self) -> None:
        cfg = self.config()
        curl = which("curl.exe") or which("curl")
        if not curl:
            raise RuntimeError("curl not found")
        corp = resolve_corporate_proxy(cfg)
        self.log("1) GitHub via Squid (expect 403)")
        r = procutil.run(
            [
                curl,
                "-sS",
                "-o",
                "NUL" if sys.platform == "win32" else "/dev/null",
                "-w",
                "   HTTP %{http_code}\n",
                "-x",
                f"http://{corp}",
                "-m",
                "10",
                "-I",
                "https://github.com",
            ]
        )
        self.log((r.stdout or r.stderr or "").rstrip() or f"exit={r.returncode}")
        self.log("2) git ls-remote")
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        r2 = procutil.run(
            ["git", "ls-remote", "https://github.com/git/git", "HEAD"],
            env=env,
        )
        out = (r2.stdout or r2.stderr or "").splitlines()[:5]
        for line in out:
            self.log(line)
