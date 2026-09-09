"""Core Windows client: tunnel / status / probe / test."""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from desktop import procutil
from desktop.config_io import (
    apply_config,
    get_http_bridge_port,
    get_local_socks_port,
    get_pac_listen_port,
    get_server,
    get_server_host,
    get_sing_box_path,
    get_socks_scope,
    get_kill_switch,
    get_tun_elevate,
    get_tun_enabled,
    get_tun_mtu,
    get_reverse_ssh,
    get_reverse_ssh_enabled,
    get_vps_proxy_ports,
    get_watchdog_enabled,
    infer_corporate,
    invoke_init,
    load_config,
    resolve_corporate_proxy,
    update_config_key,
)
from desktop.docker_env import docker_smoke_test, write_docker_env
from desktop.docker_proxy import disable_docker_desktop_proxy, enable_docker_desktop_proxy
from desktop.git_proxy import (
    git_get,
    set_git_http_proxy,
    write_cli_env,
    clear_git_proxy,
    clear_instead_of,
)
from desktop.paths import Paths, is_frozen, self_command
from desktop.reverse_ssh import ReverseSshManager
from desktop.kill_switch import allow_ips as kill_switch_allow_ips
from desktop.kill_switch import apply as apply_kill_switch
from desktop.kill_switch import clear as clear_kill_switch
from desktop.kill_switch import install_commands as kill_switch_install_cmds
from desktop.kill_switch import is_applied as kill_switch_is_applied
from desktop.kill_switch import remember_plan as remember_kill_switch_plan
from desktop.singbox_mode import SingboxModeManager, require_transport
from desktop.tun import TunManager
from desktop.sys_proxy import (
    disable_browser_proxy,
    disable_linux_env_proxy,
    enable_browser_pac,
    enable_linux_env_proxy,
)

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def _port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_port(host: str, port: int, *, timeout: float = 10.0) -> bool:
    return procutil.wait_port_open(host, port, timeout=timeout)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _find_pythonw() -> str | None:
    """Prefer pythonw.exe so child processes never flash a console."""
    if sys.platform != "win32":
        return _which("python3") or _which("python")
    candidates: list[Path] = []
    exe = Path(sys.executable)
    # Same install as current interpreter — pythonw first
    if exe.name.lower() in ("python.exe", "python3.exe"):
        pw = exe.with_name("pythonw.exe")
        if pw.is_file():
            candidates.append(pw)
    for name in ("pythonw.exe", "python.exe", "python3.exe"):
        w = shutil.which(name)
        if w and "WindowsApps" not in w:
            candidates.append(Path(w))
    candidates.append(exe)
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()).lower() if c.is_file() else ""
        if not key or key in seen or "WindowsApps" in key:
            continue
        seen.add(key)
        # Prefer pythonw
        if c.name.lower() == "pythonw.exe":
            return str(c.resolve())
    for c in candidates:
        if c.is_file() and "WindowsApps" not in str(c):
            return str(c.resolve())
    return None


class OpsClient:
    def __init__(self, paths: Paths | None = None, log: LogFn = _noop) -> None:
        self.paths = paths or Paths()
        self._user_log = log
        self.log = self._log
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
        self._atexit_done = False
        atexit.register(self._atexit_teardown)
        try:
            if not self._vpn_process_up():
                self.teardown_overrides()
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

    def probe(self, host: str, port: int = 443) -> int:
        """CONNECT probe via corporate Squid. Returns 0 on success."""
        self.reload_env()
        cfg = self.config()
        proxy = resolve_corporate_proxy(cfg)
        ph, _, pp = proxy.partition(":")
        pport = int(pp or "3128")
        self.log(f"proxy={ph}:{pport}")
        self.log(f"target={host}:{port}")
        sock = socket.create_connection((ph, pport), timeout=5)
        try:
            req = (
                f"CONNECT {host}:{port} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Proxy-Connection: keep-alive\r\n\r\n"
            ).encode("ascii")
            sock.sendall(req)
            buf = b""
            while b"\r\n\r\n" not in buf and len(buf) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        finally:
            sock.close()
        status = buf.split(b"\r\n", 1)[0].decode("ascii", "replace")
        self.log(status)
        if "200" in status:
            self.log("OK: Squid allows CONNECT to this host:port")
            return 0
        self.log("FAIL: Squid denied/failed CONNECT")
        return 1

    def _bridge_hosts(self, cfg: dict[str, Any], mode: str) -> tuple[list[str], list[str]]:
        bypass: list[str] = []
        seen: set[str] = set()
        for h in cfg.get("proxy_bypass") or []:
            if not h:
                continue
            k = str(h).lower()
            if k in seen:
                continue
            seen.add(k)
            bypass.append(str(h))
        pac: list[str] = []
        if mode == "github":
            extra = [
                "github.com",
                "*.github.com",
                "*.githubusercontent.com",
                "*.githubassets.com",
                "*.github.io",
                "ghcr.io",
                "*.ghcr.io",
                # Cursor (Anysphere) — enterprise network allowlist
                "cursor.com",
                "*.cursor.com",
                "*.cursor.sh",
                "*.cursor-cdn.com",
                "*.cursorapi.com",
                "*.cursorvm.com",
                "downloads.cursor.com",
            ]
            seen_t: set[str] = set()
            for h in list(cfg.get("blocked_hosts") or []) + extra:
                if not h:
                    continue
                k = str(h).lower()
                if k in seen_t:
                    continue
                seen_t.add(k)
                pac.append(str(h))
        return pac, bypass

    def stop_http_bridge(self) -> None:
        """Reap leftover Python HTTP→SOCKS children from older clients."""
        procutil.invalidate_proc_cache()
        http_port = get_http_bridge_port()
        targets: list[int] = []
        if self.paths.bridge_pid.is_file():
            try:
                old = int(self.paths.bridge_pid.read_text().strip())
            except ValueError:
                old = 0
            if old:
                targets.append(old)
            self.paths.bridge_pid.unlink(missing_ok=True)
        # Reap orphans: previous `on` can leave a bridge if pid-file was overwritten
        # while the old listener kept 1088 open.
        targets.extend(procutil.pids_listening_on(http_port, cache=False))
        targets.extend(procutil.pids_cmdline_match("desktop bridge", cache=False))
        targets.extend(procutil.pids_cmdline_match("-m desktop bridge", cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"http-bridge pid={pid} stopped")

    def _enable_browser_pac(
        self,
        cfg: dict[str, Any],
        http_port: int,
        *,
        pac_url: str | None = None,
    ) -> None:
        enable_browser_pac(
            http_port,
            get_socks_scope(),
            len(cfg.get("proxy_bypass") or []),
            self.paths.proxy_backup,
            log=self.log,
            pac_url=pac_url,
        )

    def stop_pac_server(self) -> None:
        procutil.invalidate_proc_cache()
        targets: list[int] = []
        if self.paths.pac_pid.is_file():
            try:
                old = int(self.paths.pac_pid.read_text().strip())
            except ValueError:
                old = 0
            if old:
                targets.append(old)
            self.paths.pac_pid.unlink(missing_ok=True)
        pac_port = get_pac_listen_port()
        targets.extend(procutil.pids_listening_on(pac_port, cache=False))
        targets.extend(procutil.pids_cmdline_match("desktop pac-serve", cache=False))
        targets.extend(procutil.pids_cmdline_match("-m desktop pac-serve", cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"pac-serve pid={pid} stopped")

    def start_pac_server(self, cfg: dict[str, Any], *, proxy_port: int) -> str:
        """Spawn PAC-only child; returns AutoConfigURL (PROXY line → proxy_port)."""
        scope = get_socks_scope()
        mode = "full" if scope == "full" else "github"
        pac_port = get_pac_listen_port()
        bypass_via = str(cfg.get("proxy_bypass_via") or "direct").strip().lower()
        if bypass_via not in ("direct", "corporate"):
            bypass_via = "direct"
        pac, bypass = self._bridge_hosts(cfg, mode)
        self.stop_pac_server()

        args = [
            *self_command(),
            "pac-serve",
            "--listen",
            f"127.0.0.1:{pac_port}",
            "--proxy-port",
            str(proxy_port),
            "--mode",
            mode,
            "--bypass-via",
            bypass_via,
            "--pid-file",
            str(self.paths.pac_pid),
        ]
        corp = resolve_corporate_proxy(cfg)
        if corp:
            args.extend(["--fallback-proxy", corp])
        for h in pac:
            args.extend(["--pac-host", str(h)])
        for h in bypass:
            args.extend(["--bypass-host", str(h)])

        if not is_frozen():
            pyw = _find_pythonw()
            if pyw and Path(args[0]).name.lower().startswith("python"):
                args[0] = pyw

        env = os.environ.copy()
        root = str(self.paths.root)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"

        proc = procutil.popen(args, env=env, cwd=root, detached=True)
        self.paths.pac_pid.write_text(str(proc.pid), encoding="utf-8")
        if not _wait_port("127.0.0.1", pac_port, timeout=5.0):
            if proc.poll() is not None:
                self.paths.pac_pid.unlink(missing_ok=True)
                raise RuntimeError(
                    f"PAC server exited (code={proc.returncode})"
                )
            procutil.kill_pid(proc.pid)
            self.paths.pac_pid.unlink(missing_ok=True)
            raise RuntimeError(f"PAC port {pac_port} never opened")
        url = f"http://127.0.0.1:{pac_port}/proxy.pac"
        self.log(f"PAC {url} → PROXY 127.0.0.1:{proxy_port}")
        return url

    def _vpn_process_up(self) -> bool:
        try:
            if self.singbox.running():
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.tun.running():
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def teardown_overrides(self) -> None:
        """Undo git / PAC / Docker / env changes. Safe if nothing was enabled."""
        try:
            disable_browser_proxy(self.paths.proxy_backup, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"PAC off: {exc}")
        try:
            disable_linux_env_proxy(self.paths.env_proxy_backup, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"env proxy off: {exc}")
        try:
            clear_git_proxy(
                self.paths.cli_env,
                self.paths.cli_ps1,
                log=self.log,
                backup_path=self.paths.git_proxy_backup,
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"git proxy off: {exc}")
        try:
            self.write_docker_helpers(http_port=get_http_bridge_port(), active=False)
        except Exception as exc:  # noqa: BLE001
            self.log(f"docker proxy off: {exc}")
        try:
            clear_instead_of(self.log)
        except Exception:  # noqa: BLE001
            pass

    def _atexit_teardown(self) -> None:
        if self._atexit_done:
            return
        self._atexit_done = True
        try:
            self.teardown_overrides()
        except Exception:  # noqa: BLE001
            pass

    def set_git_singbox(self, cfg: dict[str, Any], http_port: int) -> None:
        """Point CLI/docker/browser at sing-box HTTP inbound + PAC server."""
        if get_tun_enabled():
            # TUN already routes git. Global http.proxy only survives a crash.
            clear_git_proxy(
                self.paths.cli_env,
                self.paths.cli_ps1,
                log=self.log,
                backup_path=self.paths.git_proxy_backup,
            )
        else:
            set_git_http_proxy(
                f"http://127.0.0.1:{http_port}",
                log=self.log,
                backup_path=self.paths.git_proxy_backup,
            )
        write_cli_env(http_port, self.paths.cli_env, self.paths.cli_ps1)
        self.write_docker_helpers(cfg, http_port=http_port, active=True)
        pac_url = self.start_pac_server(cfg, proxy_port=http_port)
        self._enable_browser_pac(cfg, http_port, pac_url=pac_url)
        enable_linux_env_proxy(
            http_port,
            self.paths.env_proxy_backup,
            log=self.log,
        )
        self.log(f"git via {'TUN' if get_tun_enabled() else f'sing-box HTTP :{http_port}'}")

    def start_singbox_mode(self) -> None:
        """VLESS+Reality through Squid (sing-box)."""
        self.reload_env()
        cfg = self.config()
        server = get_server(cfg)
        host = get_server_host(cfg)
        if "YOUR_VPS" in host or not host:
            raise RuntimeError("Set real server.host in config.json")

        transport = require_transport(cfg)
        port = int(transport.get("port") or server.get("port") or 443)
        office_proxy = resolve_corporate_proxy(cfg)
        if office_proxy:
            self.log(f"Probing CONNECT {host}:{port} via proxy...")
            if self.probe(host, port) != 0:
                raise RuntimeError(
                    f"CONNECT to {host}:{port} failed.\n"
                    "On the VPS: bash modes/vps/bootstrap_singbox_443.sh "
                    "(and disable sshd on :443)"
                )

        # Avoid fighting leftover TUN processes
        try:
            if self.tun.running():
                self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"stop legacy TUN: {exc}")

        socks_port = get_local_socks_port(cfg)
        http_port = get_http_bridge_port()
        # Reap Python HTTP bridge if it holds :1088
        self.stop_http_bridge()
        self._reap_socks_orphans(socks_port)

        if not self.singbox.find_sing_box(get_sing_box_path(cfg)):
            self.log("sing-box missing — downloading…")
            self.download_sing_box()

        if infer_corporate(cfg):
            bypass = [str(h) for h in cfg.get("proxy_bypass") or [] if h]
        else:
            bypass = ["*.local", "*.lan"]
        kill_switch = get_kill_switch()
        enable_tun = get_tun_enabled() or kill_switch
        if kill_switch and not get_tun_enabled():
            self.log("kill switch: поднимаю TUN")
        prelude: list[str] = []
        if kill_switch:
            prelude = self._ensure_kill_switch(cfg)
        self.singbox.start(
            server_host=host,
            transport=transport,
            corporate_proxy=office_proxy,
            socks_port=socks_port,
            http_port=http_port,
            enable_tun=enable_tun,
            sing_box_path=get_sing_box_path(cfg),
            elevate=get_tun_elevate() if enable_tun else False,
            bypass_hosts=bypass,
            mtu=get_tun_mtu(cfg),
            vps_proxy_ports=get_vps_proxy_ports(cfg),
            force_restart=True,
            kill_switch=kill_switch,
            prelude_cmds=prelude,
        )
        self.set_git_singbox(cfg, http_port)
        self._maybe_start_reverse_ssh(cfg)
        state = {
            "mode": "singbox",
            "scope": get_socks_scope(),
            "tun": enable_tun,
            "socks": f"socks5h://127.0.0.1:{socks_port}",
            "http": f"http://127.0.0.1:{http_port}",
            "pac": f"http://127.0.0.1:{get_pac_listen_port()}/proxy.pac",
            "started": datetime.now(timezone.utc).isoformat(),
        }
        self.paths.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.log(
            f"sing-box готов: scope={get_socks_scope()} tun={int(enable_tun)} "
            f"socks=127.0.0.1:{socks_port} http=127.0.0.1:{http_port}"
        )
        self._log_singbox_tail("после запуска")
        self._probe_exit(socks_port)

    def stop_singbox_mode(self) -> None:
        try:
            self.reverse_ssh.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh stop: {exc}")
        self.singbox.stop()
        self.stop_pac_server()
        self.teardown_overrides()
        self.paths.state_path.unlink(missing_ok=True)

    def write_docker_helpers(
        self,
        cfg: dict[str, Any] | None = None,
        *,
        http_port: int | None = None,
        active: bool = True,
    ) -> None:
        """Generate var/docker.env + compose snippet + optional extra_hosts."""
        if cfg is None:
            try:
                cfg = self.config()
            except FileNotFoundError:
                cfg = {}
        port = int(http_port if http_port is not None else get_http_bridge_port())
        extra = cfg.get("docker_dns_hosts")
        dns_hosts = [str(h) for h in extra] if isinstance(extra, list) and extra else None
        write_docker_env(
            port,
            self.paths.docker_env,
            self.paths.docker_compose_proxy,
            self.paths.docker_hosts,
            dns_hosts=dns_hosts,
            active=active,
            log=self.log,
            run_ps1=self.paths.docker_run_ps1,
            run_sh=self.paths.docker_run_sh,
        )
        if active:
            enable_docker_desktop_proxy(
                port, self.paths.docker_proxy_backup, log=self.log
            )
        else:
            disable_docker_desktop_proxy(
                self.paths.docker_proxy_backup, http_port=port, log=self.log
            )

    def docker_env(self) -> None:
        """Refresh Docker helper files (proxy must already be up for full effect)."""
        self.reload_env()
        http_port = get_http_bridge_port()
        active = _port_open("127.0.0.1", http_port)
        if not active:
            self.log(
                f"HTTP bridge :{http_port} не слушает — пишу заглушку. "
                "Сначала: ergoms-secure-connection on"
            )
        self.write_docker_helpers(http_port=http_port, active=active)
        if active:
            self.log(f"env-file:  docker run --env-file {self.paths.docker_env} IMAGE")
            self.log(f"wrapper:   {self.paths.docker_run_ps1} -- IMAGE")
            self.log(
                f"compose:   -f {self.paths.docker_compose_proxy} "
                f"(<<: *ergoms-secure-connection-proxy)"
            )

    def docker_test(self) -> int:
        """Smoke-test: HTTPS from a container via the host HTTP bridge."""
        self.reload_env()
        http_port = get_http_bridge_port()
        if not _port_open("127.0.0.1", http_port):
            self.log(f"HTTP bridge :{http_port} down — сначала: ergoms-secure-connection on")
            return 2
        # Refresh helpers so docker.env has the current host IP
        self.write_docker_helpers(http_port=http_port, active=True)
        return docker_smoke_test(http_port, log=self.log)

    def _reap_socks_orphans(self, socks_port: int) -> None:
        """Free the local SOCKS port before starting sing-box."""
        procutil.invalidate_proc_cache()
        targets = list(procutil.pids_listening_on(socks_port, cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"socks orphan pid={pid} stopped")

    def enable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist:
            update_config_key(self.paths.config_path, "tun.enabled", True)
            self.log("tun.enabled=true записан в config.json")
        # Restart sing-box process with TUN inbound
        self.start_singbox_mode()

    def disable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist and get_kill_switch():
            update_config_key(self.paths.config_path, "kill_switch", False)
            self.log("kill switch выключен вместе с TUN")
        if persist:
            update_config_key(self.paths.config_path, "tun.enabled", False)
            self.log("tun.enabled=false записан в config.json")
        if self.singbox.running() or (
            self.paths.state_path.is_file()
            and "singbox"
            in self.paths.state_path.read_text(encoding="utf-8-sig", errors="ignore")
        ):
            self.start_singbox_mode()

    def download_sing_box(self) -> None:
        self.reload_env()
        proxy_url = None
        http_port = get_http_bridge_port()
        if _port_open("127.0.0.1", http_port):
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

    def watchdog_daemon_alive(self) -> bool:
        if not self.paths.watchdog_pid.is_file():
            return False
        try:
            pid = int(self.paths.watchdog_pid.read_text().strip())
        except ValueError:
            return False
        return bool(pid and procutil.pid_alive(pid))

    def stop_watchdog_daemon(self) -> None:
        targets: list[int] = []
        if self.paths.watchdog_pid.is_file():
            try:
                pid = int(self.paths.watchdog_pid.read_text().strip())
            except ValueError:
                pid = 0
            if pid and pid != os.getpid():
                targets.append(pid)
            self.paths.watchdog_pid.unlink(missing_ok=True)
        targets.extend(procutil.pids_cmdline_match("watch --daemon", cache=False))
        targets.extend(procutil.pids_cmdline_match("-m desktop watch", cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"watchdog pid={pid} остановлен")
        leftover = [p for p in targets if p != os.getpid() and procutil.pid_alive(p)]
        if leftover:
            self.log(
                f"watchdog всё ещё жив pid={','.join(str(p) for p in leftover)} "
                "— он может снова поднять sing-box"
            )

    def ensure_watchdog_daemon(self) -> None:
        """Spawn background `watch --daemon` so CLI `on` keeps monitoring after exit."""
        self.reload_env()
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
            pyw = _find_pythonw()
            if pyw and Path(args[0]).name.lower().startswith("python"):
                args[0] = pyw

        env = os.environ.copy()
        root = str(self.paths.root)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"
        env["ERGOMS_SC_WATCHDOG_CHILD"] = "1"

        proc = procutil.popen(args, env=env, cwd=root, detached=True)
        self.paths.watchdog_pid.write_text(str(proc.pid), encoding="utf-8")
        time.sleep(0.4)
        if proc.poll() is not None:
            self.paths.watchdog_pid.unlink(missing_ok=True)
            self.log(
                f"watchdog exited immediately (code={proc.returncode}); "
                "see logs/watchdog.log"
            )
            return
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
        if not self.singbox.running() and not _port_open(
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

    def _probe_exit(self, socks_port: int) -> None:
        """Best-effort SOCKS5 CONNECT so the log shows if VLESS actually works."""
        try:
            from desktop.watchdog import socks_probe
        except Exception as exc:  # noqa: BLE001
            self.log(f"проверка выхода: не удалось импортировать probe ({exc})")
            return
        self.log(f"проверка выхода через SOCKS :{socks_port} → 1.1.1.1:443…")
        err = socks_probe(socks_port, timeout=8.0)
        if err:
            self.log(f"проверка выхода: НЕ ОК — {err}")
            self._log_singbox_tail("после неудачной проверки")
        else:
            self.log("проверка выхода: OK (SOCKS CONNECT прошёл)")

    def _kill_switch_hosts(self, cfg: dict[str, Any]) -> list[str]:
        hosts = [get_server_host(cfg)]
        proxy = resolve_corporate_proxy(cfg)
        if proxy:
            hosts.append(proxy.split(":")[0])
        return kill_switch_allow_ips(*hosts)

    def _ensure_kill_switch(self, cfg: dict[str, Any]) -> list[str]:
        """Apply OS routes now if admin; otherwise return prelude for UAC wrapper."""
        allow = self._kill_switch_hosts(cfg)
        if procutil.is_admin() or kill_switch_is_applied():
            apply_kill_switch(allow, var_dir=self.paths.var_dir, log=self.log)
            return []
        cmds = kill_switch_install_cmds(allow)
        if cmds:
            remember_kill_switch_plan(self.paths.var_dir, allow)
            self.log("kill switch: маршруты поставлю вместе с UAC для TUN")
        return cmds

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
        if (tun and elevate) and (
            self.singbox.running() or self.tun.running()
        ):
            return True
        return False

    def enable(self, *, spawn_watchdog: bool = True) -> None:
        self.reload_env()
        tun = get_tun_enabled() or get_kill_switch()
        ks = get_kill_switch()
        self.log(
            f"подключение: VLESS+Reality, TUN={'вкл' if tun else 'выкл'}"
            f", kill switch={'вкл' if ks else 'выкл'}"
        )
        self.start_singbox_mode()
        if spawn_watchdog:
            if procutil.is_admin():
                self.stop_watchdog_daemon()
            self.ensure_watchdog_daemon()

    def disable(self) -> None:
        self.reload_env()
        self.log("отключение VPN…")
        self.stop_watchdog_daemon()
        stop_err: Exception | None = None
        try:
            self.stop_singbox_mode()
        except Exception as exc:  # noqa: BLE001
            stop_err = exc
            self.log(f"sing-box не остановился: {exc}")
        try:
            self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"legacy TUN: {exc}")
        self.stop_http_bridge()
        self.teardown_overrides()
        procutil.invalidate_proc_cache()
        leftover = self.singbox.pid()
        if leftover:
            raise RuntimeError(
                f"sing-box pid={leftover} всё ещё работает. "
                "При TUN нужен UAC, чтобы его остановить."
            )
        if stop_err:
            raise stop_err
        try:
            clear_kill_switch(var_dir=self.paths.var_dir, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"kill switch off: {exc}")
        self.log("VPN отключён: sing-box остановлен, PAC/git/Docker сброшены")

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
        info["kill_switch_applied"] = kill_switch_is_applied()
        lines.append(
            f"kill_switch       = {1 if info['kill_switch'] else 0}"
            + (" applied" if info["kill_switch_applied"] else "")
        )
        if include_git:
            info["git_http_proxy"] = git_get("http.proxy")
            info["git_https_proxy"] = git_get("https.proxy")
            lines.append(f"git http.proxy  = {info['git_http_proxy']}")
            lines.append(f"git https.proxy = {info['git_https_proxy']}")

        info["singbox_running"] = self.singbox.running()
        info["singbox_pid"] = self.singbox.pid()
        try:
            socks_port = get_local_socks_port()
        except Exception:  # noqa: BLE001
            socks_port = 1080
        info["socks_port"] = socks_port
        info["socks_up"] = _port_open("127.0.0.1", socks_port)
        info["http_up"] = _port_open("127.0.0.1", int(info["http_port"]))
        info["pac_up"] = _port_open("127.0.0.1", int(info["pac_port"]))
        if info["singbox_running"]:
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
                lines.append(
                    f"PAC             = http://127.0.0.1:{info['pac_port']}/proxy.pac"
                )

        # TUN inbound inside sing-box
        info["tun_running"] = bool(info["singbox_running"] and get_tun_enabled())
        info["tun_pid"] = self.tun.pid() or (
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
            cfg = self.config()
            info["corporate_proxy"] = resolve_corporate_proxy(cfg)
            host = get_server_host(cfg)
            port = int(get_server(cfg).get("port") or 443)
            info["server_target"] = f"{host}:{port}"
            info["ssh_target"] = info["server_target"]  # GUI compat
            info["proxy_bypass_n"] = len(cfg.get("proxy_bypass") or [])
            info["sing_box_path"] = get_sing_box_path(cfg)
            tr = cfg.get("transport") or {}
            info["transport_type"] = str(tr.get("type") or "")
            lines.append(f"corporate_proxy = {info['corporate_proxy']}")
            lines.append(f"server          = {info['server_target']}")
            uuid = str(tr.get("uuid") or "")
            uuid_show = (uuid[:8] + "…") if len(uuid) > 8 else (uuid or "(empty)")
            lines.append(
                f"transport       = {info['transport_type'] or 'vless-reality'} "
                f"uuid={uuid_show} sni={tr.get('server_name') or ''}"
            )
            lines.append(f"proxy_bypass    = {info['proxy_bypass_n']} entries")
            if info["sing_box_path"]:
                lines.append(f"sing_box_path   = {info['sing_box_path']}")

        if self.paths.docker_env.is_file():
            lines.append(f"docker.env      = {self.paths.docker_env}")
        if self.paths.docker_compose_proxy.is_file():
            lines.append(f"docker compose  = {self.paths.docker_compose_proxy}")

        info["active"] = bool(info.get("singbox_running") or info.get("tun_running"))
        return info

    def test_bypass(self) -> None:
        cfg = self.config()
        curl = _which("curl.exe") or _which("curl")
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
