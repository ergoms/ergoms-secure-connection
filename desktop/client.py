"""Core Windows client: tunnel / status / probe / test."""

from __future__ import annotations

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
from desktop.bridge import HttpBridge
from desktop.config_io import (
    apply_config,
    get_http_bridge_port,
    get_local_socks_port,
    get_mode,
    get_pac_listen_port,
    get_server,
    get_server_host,
    get_sing_box_path,
    get_socks_scope,
    get_tun_elevate,
    get_tun_enabled,
    get_tun_mtu,
    get_reverse_ssh,
    get_reverse_ssh_enabled,
    get_vps_proxy_ports,
    get_watchdog_enabled,
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
from desktop.paths import Paths, bundle_dir, is_frozen, resolve_ssh_identity, self_command
from desktop.reverse_ssh import ReverseSshManager
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
    """Prefer pythonw.exe so ProxyCommand never flashes a console."""
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


def _ensure_connect_script(paths: Paths) -> Path:
    """Writable connect_proxy.py next to data (copy from bundle if needed)."""
    dst = paths.connect_py
    if dst.is_file():
        return dst
    src = bundle_dir() / "lib" / "connect_proxy.py"
    if not src.is_file():
        raise RuntimeError(f"connect_proxy.py not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


class OpsClient:
    def __init__(self, paths: Paths | None = None, log: LogFn = _noop) -> None:
        self.paths = paths or Paths()
        self.log = log
        self.bridge = HttpBridge()
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

    def proxy_command(self, cfg: dict[str, Any]) -> str:
        """SSH ProxyCommand via pythonw + connect_proxy.py (no GUI exe flash)."""
        allow_port = int(cfg.get("ssh", {}).get("port") or 443)
        host = cfg["ssh"]["host"]
        os.environ["OPS_CONTENT_HTTP_PROXY"] = resolve_corporate_proxy(cfg)
        os.environ["OPS_CONTENT_CONNECT_ALLOW"] = f"{host}:{allow_port}"

        py = _find_pythonw()
        connect_py = _ensure_connect_script(self.paths)

        # Prefer pythonw + .py (no console). Avoid OpsContent.exe here — onefile
        # extract flashes a window on every SSH ProxyCommand.
        if py and connect_py.is_file():
            cmd_path = self.paths.proxy_cmd
            cmd_path.write_text(
                "\r\n".join(
                    [
                        "@echo off",
                        f"set OPS_CONTENT_HTTP_PROXY={resolve_corporate_proxy(cfg)}",
                        f"set OPS_CONTENT_CONNECT_ALLOW={host}:{allow_port}",
                        f'"{py}" "{connect_py}" %1 %2',
                    ]
                )
                + "\r\n",
                encoding="ascii",
            )
            return f'"{py}" "{connect_py}" %h %p'

        if is_frozen():
            exe = str(Path(sys.executable).resolve())
            return f'"{exe}" connect %h %p'

        raise RuntimeError("Python not found for SSH ProxyCommand (need pythonw/python)")

    def ssh_args(self, cfg: dict[str, Any]) -> list[str]:
        ssh = cfg.get("ssh") or {}
        port = str(ssh.get("port") or 443)
        socks = int(ssh.get("local_socks_port") or 1080)
        host = ssh["host"]
        user = ssh.get("user") or "root"
        os.environ["OPS_CONTENT_CONNECT_ALLOW"] = f"{host}:{port}"
        kh = str(self.paths.known_hosts).replace("\\", "/")
        gkh = "NUL" if sys.platform == "win32" else "/dev/null"
        proxy = self.proxy_command(cfg)
        args = [
            "-N",
            "-D",
            f"127.0.0.1:{socks}",
            "-p",
            port,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=20",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={kh}",
            "-o",
            f"GlobalKnownHostsFile={gkh}",
            "-o",
            "UpdateHostKeys=yes",
            "-o",
            "Compression=no",
            "-o",
            "IPQoS=throughput",
            "-o",
            "TCPKeepAlive=yes",
            "-o",
            "Ciphers=chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com,aes128-ctr",
            "-o",
            "HostKeyAlgorithms=ssh-ed25519,rsa-sha2-512,rsa-sha2-256",
            "-o",
            f"ProxyCommand={proxy}",
        ]
        ident = resolve_ssh_identity(self.paths.creds_dir)
        if ident:
            args += ["-o", "IdentitiesOnly=yes", "-i", ident]
        args.append(f"{user}@{host}")
        return args

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
        if port == 22:
            self.log("Tip: try port 443 (sshd on 443).")
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

    def start_http_bridge(self, cfg: dict[str, Any]) -> int:
        """Spawn HTTP→SOCKS bridge as a child process (survives CLI exit)."""
        scope = get_socks_scope()
        mode = "full" if scope == "full" else "github"
        socks_port = int((cfg.get("ssh") or {}).get("local_socks_port") or 1080)
        http_port = get_http_bridge_port()
        bypass_via = str(cfg.get("proxy_bypass_via") or "direct").strip().lower()
        if bypass_via not in ("direct", "corporate"):
            bypass_via = "direct"
        pac, bypass = self._bridge_hosts(cfg, mode)
        self.stop_http_bridge()

        args = [
            *self_command(),
            "bridge",
            "--listen",
            f"127.0.0.1:{http_port}",
            "--socks",
            f"127.0.0.1:{socks_port}",
            "--mode",
            mode,
            "--bypass-via",
            bypass_via,
        ]
        corp = resolve_corporate_proxy(cfg)
        if corp:
            args.extend(["--fallback-proxy", corp])
        for h in pac:
            args.extend(["--pac-host", str(h)])
        for h in bypass:
            args.extend(["--bypass-host", str(h)])

        # Prefer pythonw so the bridge child never flashes a console.
        if not is_frozen():
            pyw = _find_pythonw()
            if pyw and Path(args[0]).name.lower().startswith("python"):
                args[0] = pyw

        env = os.environ.copy()
        root = str(self.paths.root)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"

        proc = procutil.popen(args, env=env, cwd=root, detached=True)
        self.paths.bridge_pid.write_text(str(proc.pid), encoding="utf-8")
        for _ in range(5):
            if proc.poll() is not None:
                self.paths.bridge_pid.unlink(missing_ok=True)
                raise RuntimeError(
                    f"HTTP bridge exited immediately (code={proc.returncode})"
                )
            if _port_open("127.0.0.1", http_port):
                self.log(
                    f"HTTP bridge 127.0.0.1:{http_port} mode={mode} "
                    f"socks=127.0.0.1:{socks_port} bypass={len(bypass)}"
                )
                return http_port
            time.sleep(0.05)
        if _wait_port("127.0.0.1", http_port, timeout=4.0):
            self.log(
                f"HTTP bridge 127.0.0.1:{http_port} mode={mode} "
                f"socks=127.0.0.1:{socks_port} bypass={len(bypass)}"
            )
            return http_port
        if proc.poll() is not None:
            self.paths.bridge_pid.unlink(missing_ok=True)
            raise RuntimeError(
                f"HTTP bridge exited (code={proc.returncode})"
            )
        procutil.kill_pid(proc.pid)
        self.paths.bridge_pid.unlink(missing_ok=True)
        raise RuntimeError(f"HTTP bridge port {http_port} never opened")

    def stop_http_bridge(self) -> None:
        procutil.invalidate_proc_cache()
        self.bridge.stop(log=self.log)
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

    def set_git_singbox(self, cfg: dict[str, Any], http_port: int) -> None:
        """Point git/CLI/docker/browser at sing-box HTTP inbound + PAC server."""
        proxy = f"http://127.0.0.1:{http_port}"
        set_git_http_proxy(proxy, log=self.log)
        write_cli_env(http_port, self.paths.cli_env, self.paths.cli_ps1)
        self.write_docker_helpers(cfg, http_port=http_port, active=True)
        pac_url = self.start_pac_server(cfg, proxy_port=http_port)
        self._enable_browser_pac(cfg, http_port, pac_url=pac_url)
        enable_linux_env_proxy(
            http_port,
            self.paths.env_proxy_backup,
            log=self.log,
        )
        self.log(f"git via sing-box HTTP :{http_port}, scope={get_socks_scope()}")

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
        self.log(f"Probing CONNECT {host}:{port} via Squid...")
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

        bypass = [str(h) for h in cfg.get("proxy_bypass") or [] if h]
        enable_tun = get_tun_enabled()
        self.singbox.start(
            server_host=host,
            transport=transport,
            corporate_proxy=resolve_corporate_proxy(cfg),
            socks_port=socks_port,
            http_port=http_port,
            enable_tun=enable_tun,
            sing_box_path=get_sing_box_path(cfg),
            elevate=get_tun_elevate() if enable_tun else False,
            bypass_hosts=bypass,
            mtu=get_tun_mtu(cfg),
            vps_proxy_ports=get_vps_proxy_ports(cfg),
            force_restart=True,
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
            f"singbox ready scope={get_socks_scope()} tun={int(enable_tun)} "
            f"socks=:{socks_port} http=:{http_port}"
        )

    def stop_singbox_mode(self) -> None:
        try:
            self.reverse_ssh.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh stop: {exc}")
        try:
            self.singbox.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"singbox stop: {exc}")
        self.stop_pac_server()
        disable_browser_proxy(self.paths.proxy_backup, log=self.log)
        disable_linux_env_proxy(self.paths.env_proxy_backup, log=self.log)
        clear_git_proxy(self.paths.cli_env, self.paths.cli_ps1, log=self.log)
        self.write_docker_helpers(http_port=get_http_bridge_port(), active=False)
        clear_instead_of(self.log)
        self.paths.state_path.unlink(missing_ok=True)

    def set_git_socks(self, cfg: dict[str, Any]) -> int:
        http_port = self.start_http_bridge(cfg)
        proxy = f"http://127.0.0.1:{http_port}"
        set_git_http_proxy(proxy, log=self.log)
        write_cli_env(http_port, self.paths.cli_env, self.paths.cli_ps1)
        self.write_docker_helpers(cfg, http_port=http_port, active=True)
        self._enable_browser_pac(cfg, http_port)
        # Override corporate Squid in /etc/environment so ergoms/curl see the bridge
        enable_linux_env_proxy(
            http_port,
            self.paths.env_proxy_backup,
            log=self.log,
        )
        socks = int((cfg.get("ssh") or {}).get("local_socks_port") or 1080)
        self.log(f"git via SOCKS {socks}, scope={get_socks_scope()}")
        return http_port

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
                "Сначала: ops-content on"
            )
        self.write_docker_helpers(http_port=http_port, active=active)
        if active:
            self.log(f"env-file:  docker run --env-file {self.paths.docker_env} IMAGE")
            self.log(f"wrapper:   {self.paths.docker_run_ps1} -- IMAGE")
            self.log(
                f"compose:   -f {self.paths.docker_compose_proxy} "
                f"(<<: *ops-content-proxy)"
            )

    def docker_test(self) -> int:
        """Smoke-test: HTTPS from a container via the host HTTP bridge."""
        self.reload_env()
        http_port = get_http_bridge_port()
        if not _port_open("127.0.0.1", http_port):
            self.log(f"HTTP bridge :{http_port} down — сначала: ops-content on")
            return 2
        # Refresh helpers so docker.env has the current host IP
        self.write_docker_helpers(http_port=http_port, active=True)
        return docker_smoke_test(http_port, log=self.log)

    def start_tunnel(self) -> None:
        self.reload_env()
        cfg = self.config()
        ssh = cfg.get("ssh") or {}
        host = str(ssh.get("host") or "")
        if "YOUR_VPS" in host:
            raise RuntimeError("Set real ssh.host in config.json (ssh.port=443)")

        port = int(ssh.get("port") or 443)
        self.log(f"Probing CONNECT {host}:{port} via Squid...")
        if self.probe(host, port) != 0:
            raise RuntimeError(
                f"CONNECT to {host}:{port} failed (403=ACL, 503=nothing listening).\n"
                "On the VPS run: bash modes/vps/bootstrap_sshd_443.sh"
            )

        socks_port = int(ssh.get("local_socks_port") or 1080)
        if self.paths.ssh_pid.is_file():
            try:
                old = int(self.paths.ssh_pid.read_text().strip())
            except ValueError:
                old = 0
            listeners = procutil.pids_listening_on(socks_port, cache=False)
            if (
                old
                and procutil.pid_alive(old)
                and _port_open("127.0.0.1", socks_port)
                and (not listeners or old in listeners)
            ):
                self.log(f"Tunnel already running (pid={old})")
                self.set_git_socks(cfg)
                return

        # Stale pid-file / orphan ssh holding :1080 would make a new -D "succeed"
        # against the old listener while our ssh then dies.
        self._reap_socks_orphans(socks_port)

        if not _which("ssh"):
            raise RuntimeError("ssh not found in PATH (install OpenSSH Client)")

        os.environ["OPS_CONTENT_HTTP_PROXY"] = resolve_corporate_proxy(cfg)
        args = self.ssh_args(cfg)
        self.log(f"SSH SOCKS -> 127.0.0.1:{socks_port} via Squid")
        self.log(f"ProxyCommand: {self.proxy_command(cfg)}")

        proc = procutil.popen(["ssh", *args])
        self.paths.ssh_pid.write_text(str(proc.pid), encoding="utf-8")

        if proc.poll() is not None:
            self.paths.ssh_pid.unlink(missing_ok=True)
            raise RuntimeError("ssh exited immediately; check user/key/sshd")
        ok = False
        if _wait_port("127.0.0.1", socks_port, timeout=10.0):
            ok = proc.poll() is None
        if not ok or proc.poll() is not None:
            procutil.kill_pid(proc.pid)
            self.paths.ssh_pid.unlink(missing_ok=True)
            raise RuntimeError(
                f"SSH SOCKS port {socks_port} never opened. "
                f"Check key, sshd on :443, user={ssh.get('user')}"
            )

        http_port = self.set_git_socks(cfg)
        state = {
            "mode": "ssh",
            "scope": get_socks_scope(),
            "pid": proc.pid,
            "socks": f"socks5h://127.0.0.1:{socks_port}",
            "http": f"http://127.0.0.1:{http_port}",
            "started": datetime.now(timezone.utc).isoformat(),
        }
        self.paths.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.log(f"SSH tunnel ready scope={get_socks_scope()}")

    def _reap_socks_orphans(self, socks_port: int) -> None:
        """Kill ssh/ProxyCommand leftovers that still own the SOCKS port."""
        procutil.invalidate_proc_cache()
        targets: list[int] = []
        if self.paths.ssh_pid.is_file():
            try:
                old = int(self.paths.ssh_pid.read_text().strip())
            except ValueError:
                old = 0
            if old:
                targets.append(old)
            self.paths.ssh_pid.unlink(missing_ok=True)
        targets.extend(procutil.pids_listening_on(socks_port, cache=False))
        # Match our dynamic forward even if LISTEN owner lookup failed.
        targets.extend(procutil.pids_cmdline_match(f"-D 127.0.0.1:{socks_port}", cache=False))
        targets.extend(procutil.pids_cmdline_match("connect_proxy.py", cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"ssh/orphan pid={pid} stopped")

    def stop_tunnel(self) -> None:
        # Also tear down MODE=singbox if it was left running
        try:
            if self.singbox.running():
                self.stop_singbox_mode()
                return
        except Exception as exc:  # noqa: BLE001
            self.log(f"singbox stop: {exc}")
        try:
            self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"TUN stop: {exc}")
        disable_browser_proxy(self.paths.proxy_backup, log=self.log)
        disable_linux_env_proxy(self.paths.env_proxy_backup, log=self.log)
        self.stop_http_bridge()
        self.stop_pac_server()
        socks_port = int((self.config().get("ssh") or {}).get("local_socks_port") or 1080)
        self._reap_socks_orphans(socks_port)
        clear_git_proxy(self.paths.cli_env, self.paths.cli_ps1, log=self.log)
        self.write_docker_helpers(http_port=get_http_bridge_port(), active=False)
        clear_instead_of(self.log)
        self.paths.state_path.unlink(missing_ok=True)

    def enable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist:
            update_config_key(self.paths.config_path, "tun.enabled", True)
            self.log("tun.enabled=true записан в config.json")
        # Restart sing-box process with TUN inbound
        self.start_singbox_mode()

    def disable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
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
        cfg = self.config()
        cfg.setdefault("tun", {})
        cfg["tun"]["sing_box_path"] = ""
        from desktop.config_io import save_config

        save_config(self.paths.config_path, cfg)
        self.log(f"sing-box ready (auto): {path}")

    def _maybe_autostart_tun(self) -> None:
        self.reload_env()
        if not get_tun_enabled():
            self.log("tun.enabled=false — системный TUN не поднимаем")
            self.log(
                "Без TUN DNS в Docker Desktop часто мёртв "
                "(getent/pip к внешним именам). Нужен tun.enabled=true или HTTP_PROXY из var/docker.env"
            )
            return
        self.log("tun.enabled=true — поднимаем TUN поверх SOCKS (нужен для DNS в Docker)")
        try:
            self.enable_tun(persist=False)
        except Exception as exc:  # noqa: BLE001
            # Tunnel already up — don't fail the whole `on` because of TUN
            self.log(f"TUN auto-start failed: {exc}")
            self.log(
                "SOCKS оставлен. Без TUN контейнеры не резолвят pypi.org — "
                "повторите tun-on (UAC) или используйте var/docker.env (HTTP_PROXY)."
            )
            self.log("Нужен sing-box: download-sing-box / tools/")

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
            self.log(f"watchdog pid={pid} stopped")

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
        env["OPS_CONTENT_WATCHDOG_CHILD"] = "1"

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
            raise RuntimeError("Сначала включите туннель: ops-content on")
        self.reverse_ssh.start(cfg)

    def disable_reverse_ssh(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist:
            update_config_key(self.paths.config_path, "reverse_ssh.enabled", False)
            self.log("reverse_ssh.enabled=false в config.json")
        self.reverse_ssh.stop()

    def enable(self, *, spawn_watchdog: bool = True) -> None:
        self.reload_env()
        self.log(f"VLESS+Reality TUN={1 if get_tun_enabled() else 0}")
        self.start_singbox_mode()
        if spawn_watchdog:
            self.ensure_watchdog_daemon()

    def disable(self) -> None:
        self.reload_env()
        self.log("off")
        self.stop_watchdog_daemon()
        self.stop_singbox_mode()
        try:
            self.tun.stop()
        except Exception:  # noqa: BLE001
            pass
        self.stop_http_bridge()

    def status(self, *, include_git: bool = True) -> dict[str, Any]:
        """Snapshot of tunnel state.

        include_git=False skips spawning `git config` (faster for GUI polling).
        """
        self.reload_env()
        mode = get_mode()
        info: dict[str, Any] = {
            "mode": mode,
            "socks_scope": get_socks_scope(),
            "git_http_proxy": "",
            "git_https_proxy": "",
            "ssh_running": False,
            "ssh_pid": None,
            "bridge_running": False,
            "bridge_pid": None,
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
        if include_git:
            info["git_http_proxy"] = git_get("http.proxy")
            info["git_https_proxy"] = git_get("https.proxy")
            lines.append(f"git http.proxy  = {info['git_http_proxy']}")
            lines.append(f"git https.proxy = {info['git_https_proxy']}")

        if self.bridge.running:
            info["bridge_running"] = True
            info["bridge_pid"] = os.getpid()
            lines.append(f"http-bridge in-process (port={info['http_port']})")
        elif self.paths.bridge_pid.is_file():
            try:
                bpid = int(self.paths.bridge_pid.read_text().strip())
            except ValueError:
                bpid = 0
            info["bridge_pid"] = bpid
            alive = bool(bpid and procutil.pid_alive(bpid))
            listening = False
            if not alive:
                listening = _port_open("127.0.0.1", int(info["http_port"]))
            if alive or listening:
                info["bridge_running"] = True
                lines.append(
                    f"http-bridge pid={bpid} "
                    f"{'running' if alive else 'port-open'} "
                    f"(port={info['http_port']})"
                )
            else:
                lines.append(f"bridge pid={bpid} dead")

        info["singbox_running"] = self.singbox.running()
        info["singbox_pid"] = self.singbox.pid()
        if info["singbox_running"]:
            lines.append(f"singbox mode pid={info['singbox_pid']} running")
            if _port_open("127.0.0.1", int(info["http_port"])):
                lines.append(f"singbox HTTP     = 127.0.0.1:{info['http_port']}")
            if _port_open("127.0.0.1", int(info["pac_port"])):
                lines.append(
                    f"PAC             = http://127.0.0.1:{info['pac_port']}/proxy.pac"
                )

        # TUN inbound inside sing-box
        info["tun_running"] = self.tun.running() or (
            info["singbox_running"] and self.singbox.tun_active()
        )
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
                    "(tun-on или ops-content on)"
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

        info["active"] = bool(
            info["bridge_running"]
            or info.get("singbox_running")
            or info.get("tun_running")
        )
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
