"""PAC / git / Docker overrides for OpsClient."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

from desktop import procutil
from desktop.client_util import find_pythonw, pid_from_file, wait_port
from desktop.config_io import (
    get_docker_proxy_enabled,
    get_git_proxy_enabled,
    get_http_bridge_port,
    get_pac_listen_port,
    get_socks_scope,
    get_tun_enabled,
    resolve_corporate_proxy,
)
from desktop.git_proxy import clear_git_proxy, set_git_http_proxy, write_cli_env
from desktop.paths import is_frozen, self_command
from desktop.sys_proxy import (
    disable_browser_proxy,
    disable_linux_env_proxy,
    enable_browser_pac,
    enable_linux_env_proxy,
)
from desktop.tun import wait_tun_iface
from lib.netutil import port_open


class IntegrationOps:
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
        http_port = get_http_bridge_port()
        targets: list[int] = []
        old = pid_from_file(self.paths.bridge_pid, unlink=True)
        if old:
            targets.append(old)
        targets.extend(procutil.pids_listening_on(http_port, cache=False))
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
            self._pac_mode(cfg),
            len(cfg.get("proxy_bypass") or []),
            self.paths.proxy_backup,
            log=self.log,
            pac_url=pac_url,
        )


    def stop_pac_server(self) -> None:
        if self._pac_server is not None:
            try:
                self._pac_server.stop(log=self.log)
            except Exception as exc:  # noqa: BLE001
                self.log(f"PAC off: {exc}")
            self._pac_server = None
            return
        if self._inprocess_helpers:
            return
        targets: list[int] = []
        old = pid_from_file(self.paths.pac_pid, unlink=True)
        if old:
            targets.append(old)
        pac_port = get_pac_listen_port()
        targets.extend(procutil.pids_listening_on(pac_port, cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"pac-serve pid={pid} stopped")


    def _pac_mode(self, cfg: dict[str, Any] | None = None) -> str:
        """Office+TUN: whole browser via VLESS. github-only PAC sends the rest DIRECT."""
        src = cfg
        office = bool(resolve_corporate_proxy(src))
        if office and get_tun_enabled(src):
            return "full"
        return "full" if get_socks_scope(src) == "full" else "github"

    def _start_pac_inprocess(self, cfg: dict[str, Any], *, proxy_port: int) -> str:
        from desktop.pac_serve import PacServer
        from lib.pac import build_pac

        mode = self._pac_mode(cfg)
        pac_port = get_pac_listen_port()
        bypass_via = str(cfg.get("proxy_bypass_via") or "direct").strip().lower()
        if bypass_via not in ("direct", "corporate"):
            bypass_via = "direct"
        pac_hosts, bypass = self._bridge_hosts(cfg, mode)
        corp = resolve_corporate_proxy(cfg)
        body = build_pac(
            int(proxy_port),
            mode,
            pac_hosts,
            bypass,
            corp,
            bypass_via,
        )
        url = f"http://127.0.0.1:{pac_port}/proxy.pac"
        srv = getattr(self, "_pac_server", None)
        if (
            srv is not None
            and getattr(srv, "running", False)
            and getattr(srv, "port", None) == pac_port
        ):
            srv.replace_pac(body)
            return url
        if port_open("127.0.0.1", pac_port, timeout=0.2) and srv is None:
            return url
        self.stop_pac_server()
        srv = PacServer()
        srv.start(body, listen_host="127.0.0.1", listen_port=pac_port, log=self.log)
        self._pac_server = srv
        self.log(f"PAC {url} → PROXY 127.0.0.1:{proxy_port}")
        return url


    def start_pac_server(self, cfg: dict[str, Any], *, proxy_port: int) -> str:
        """Spawn PAC-only child; returns AutoConfigURL (PROXY line → proxy_port)."""
        if self._inprocess_helpers:
            return self._start_pac_inprocess(cfg, proxy_port=proxy_port)
        mode = self._pac_mode(cfg)
        pac_port = get_pac_listen_port()
        url = f"http://127.0.0.1:{pac_port}/proxy.pac"
        if port_open("127.0.0.1", pac_port, timeout=0.2):
            return url
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
            pyw = find_pythonw()
            if pyw and Path(args[0]).name.lower().startswith("python"):
                args[0] = pyw

        env = os.environ.copy()
        root = str(self.paths.root)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"

        proc = procutil.popen(args, env=env, cwd=root, detached=True)
        self.paths.pac_pid.write_text(str(proc.pid), encoding="utf-8")
        if not wait_port("127.0.0.1", pac_port, timeout=5.0):
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


    def _override_markers(self) -> bool:
        return any(
            path.is_file()
            for path in (
                self.paths.proxy_backup,
                self.paths.env_proxy_backup,
                self.paths.git_proxy_backup,
                self.paths.docker_proxy_backup,
                self.paths.cli_env,
                self.paths.cli_ps1,
                self.paths.state_path,
            )
        )


    def teardown_overrides_if_dirty(self) -> None:
        """Skip git/PAC/Docker undo when there are no leftover markers."""
        if not self._override_markers():
            return
        try:
            if self._vpn_process_up():
                return
        except Exception:  # noqa: BLE001
            pass
        self.teardown_overrides()


    def ensure_office_browser_pac(self) -> None:
        """Re-pin office PAC if another process tore it down while VPN is up."""
        try:
            if not self._vpn_process_up():
                return
            cfg = self.config()
        except Exception:  # noqa: BLE001
            return
        if not resolve_corporate_proxy(cfg):
            return
        http_port = get_http_bridge_port()
        pac_port = get_pac_listen_port()
        url = f"http://127.0.0.1:{pac_port}/proxy.pac"
        repaired = False
        if not port_open("127.0.0.1", pac_port, timeout=0.2):
            try:
                self.start_pac_server(cfg, proxy_port=http_port)
                repaired = True
            except Exception as exc:  # noqa: BLE001
                self.log(f"PAC: не поднял :{pac_port} ({exc})")
                return
        if sys.platform == "win32":
            from desktop.win_proxy import pac_url_active

            if not pac_url_active(url):
                self._enable_browser_pac(cfg, http_port, pac_url=url)
                repaired = True
        if repaired:
            self.log("PAC браузера вернул — его снял другой процесс")


    def teardown_overrides(self) -> None:
        """Undo git / PAC / Docker / env changes. Safe if nothing was enabled."""
        if not self._teardown_lock.acquire(blocking=False):
            return
        try:
            try:
                disable_browser_proxy(self.paths.proxy_backup, log=self.log)
            except Exception as exc:  # noqa: BLE001
                self.log(f"PAC off: {exc}")
            try:
                disable_linux_env_proxy(self.paths.env_proxy_backup, log=self.log)
            except Exception as exc:  # noqa: BLE001
                self.log(f"env proxy off: {exc}")
            git_marked = (
                self.paths.git_proxy_backup.is_file()
                or self.paths.cli_env.is_file()
                or self.paths.cli_ps1.is_file()
            )
            if git_marked or get_git_proxy_enabled():
                try:
                    clear_git_proxy(
                        self.paths.cli_env,
                        self.paths.cli_ps1,
                        log=self.log,
                        backup_path=self.paths.git_proxy_backup,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.log(f"git proxy off: {exc}")
            docker_marked = self.paths.docker_proxy_backup.is_file()
            if docker_marked or get_docker_proxy_enabled():
                try:
                    self.write_docker_helpers(
                        http_port=get_http_bridge_port(), active=False
                    )
                except Exception as exc:  # noqa: BLE001
                    self.log(f"docker proxy off: {exc}")
        finally:
            self._teardown_lock.release()


    def _atexit_teardown(self) -> None:
        if self._atexit_done:
            return
        self._atexit_done = True
        try:
            self.teardown_overrides_if_dirty()
        except Exception:  # noqa: BLE001
            pass


    def set_git_singbox(self, cfg: dict[str, Any], http_port: int) -> None:
        """Point CLI/docker/browser at sing-box HTTP inbound + PAC server."""
        self._apply_integrations(cfg, http_port)


    def _apply_integrations(self, cfg: dict[str, Any], http_port: int) -> None:
        """Git / Docker / PAC after sing-box is up. Git and Docker are optional."""
        if get_git_proxy_enabled(cfg):
            if get_tun_enabled():
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
            self.log(
                f"git via {'TUN' if get_tun_enabled() else f'sing-box HTTP :{http_port}'}"
            )
        elif (
            self.paths.git_proxy_backup.is_file()
            or self.paths.cli_env.is_file()
            or self.paths.cli_ps1.is_file()
        ):
            try:
                clear_git_proxy(
                    self.paths.cli_env,
                    self.paths.cli_ps1,
                    log=self.log,
                    backup_path=self.paths.git_proxy_backup,
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"git proxy off: {exc}")

        if get_docker_proxy_enabled(cfg):
            def _docker_bg() -> None:
                try:
                    self.write_docker_helpers(cfg, http_port=http_port, active=True)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"docker proxy: {exc}")

            threading.Thread(target=_docker_bg, daemon=True).start()
        elif self.paths.docker_proxy_backup.is_file():
            try:
                self.write_docker_helpers(cfg, http_port=http_port, active=False)
            except Exception as exc:  # noqa: BLE001
                self.log(f"docker proxy off: {exc}")

        # TUN already carries browser/CLI traffic. PAC + Windows Internet
        # Settings look like a system proxy and fight the tunnel.
        tun_live = False
        if sys.platform == "win32":
            tun_live = bool(wait_tun_iface(timeout=0.05))
        elif get_tun_enabled(cfg):
            tun_live = True
        office = bool(resolve_corporate_proxy(cfg))
        if tun_live and not office:
            self.stop_pac_server()
            try:
                disable_browser_proxy(self.paths.proxy_backup, log=self.log)
            except Exception as exc:  # noqa: BLE001
                self.log(f"PAC off (TUN): {exc}")
            try:
                disable_linux_env_proxy(self.paths.env_proxy_backup, log=self.log)
            except Exception as exc:  # noqa: BLE001
                self.log(f"env proxy off (TUN): {exc}")
            self.log("системный прокси не ставится — трафик через TUN")
            return
        if tun_live and office:
            self.log("офис: PAC на весь трафик (не только GitHub) — браузер не умеет TUN")
        pac_url = self.start_pac_server(cfg, proxy_port=http_port)
        self._enable_browser_pac(cfg, http_port, pac_url=pac_url)
        enable_linux_env_proxy(
            http_port,
            self.paths.env_proxy_backup,
            log=self.log,
        )


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
        from desktop.docker_env import write_docker_env
        from desktop.docker_proxy import (
            disable_docker_desktop_proxy,
            enable_docker_desktop_proxy,
        )

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
        active = port_open("127.0.0.1", http_port)
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
        if not port_open("127.0.0.1", http_port):
            self.log(f"HTTP bridge :{http_port} down — сначала: ergoms-secure-connection on")
            return 2
        # Refresh helpers so docker.env has the current host IP
        self.write_docker_helpers(http_port=http_port, active=True)
        from desktop.docker_env import docker_smoke_test

        return docker_smoke_test(http_port, log=self.log)

