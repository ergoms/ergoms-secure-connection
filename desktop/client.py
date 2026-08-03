"""Core Windows client: tunnel / relay / status / probe / test."""

from __future__ import annotations

import json
import os
import re
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
    apply_dotenv,
    get_http_bridge_port,
    get_mode,
    get_sing_box_path,
    get_socks_scope,
    get_tun_elevate,
    get_tun_enabled,
    invoke_init,
    load_config,
    resolve_corporate_proxy,
    update_env_key,
)
from desktop.git_proxy import (
    disable_relay_git,
    enable_relay_git,
    git_get,
    set_git_http_proxy,
    write_cli_env,
    clear_git_proxy,
    clear_instead_of,
)
from desktop.paths import Paths, bundle_dir, is_frozen, self_command
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
        apply_dotenv(self.paths.env_path)
        self.tun = TunManager(
            self.paths.var_dir,
            self.paths.tools_dir,
            self.paths.logs_dir,
            log=self.log,
        )
    def reload_env(self) -> None:
        apply_dotenv(self.paths.env_path)

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
            "ServerAliveInterval=30",
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
        ident = (ssh.get("identity_file") or "").strip()
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
        sock = socket.create_connection((ph, pport), timeout=15)
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
        for _ in range(30):
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
            time.sleep(0.15)
        procutil.kill_pid(proc.pid)
        self.paths.bridge_pid.unlink(missing_ok=True)
        raise RuntimeError(f"HTTP bridge port {http_port} never opened")

    def stop_http_bridge(self) -> None:
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
        targets.extend(procutil.pids_listening_on(http_port))
        targets.extend(procutil.pids_cmdline_match("desktop bridge"))
        targets.extend(procutil.pids_cmdline_match("-m desktop bridge"))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"http-bridge pid={pid} stopped")

    def set_git_socks(self, cfg: dict[str, Any]) -> int:
        http_port = self.start_http_bridge(cfg)
        proxy = f"http://127.0.0.1:{http_port}"
        set_git_http_proxy(proxy, log=self.log)
        write_cli_env(http_port, self.paths.cli_env, self.paths.cli_ps1)
        enable_browser_pac(
            http_port,
            get_socks_scope(),
            len(cfg.get("proxy_bypass") or []),
            self.paths.proxy_backup,
            log=self.log,
        )
        # Override corporate Squid in /etc/environment so ergoms/curl see the bridge
        enable_linux_env_proxy(
            http_port,
            self.paths.env_proxy_backup,
            log=self.log,
        )
        socks = int((cfg.get("ssh") or {}).get("local_socks_port") or 1080)
        self.log(f"git via SOCKS {socks}, scope={get_socks_scope()}")
        return http_port

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
            listeners = procutil.pids_listening_on(socks_port)
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

        ok = False
        for _ in range(40):
            time.sleep(0.25)
            if proc.poll() is not None:
                self.paths.ssh_pid.unlink(missing_ok=True)
                raise RuntimeError("ssh exited immediately; check user/key/sshd")
            if _port_open("127.0.0.1", socks_port):
                # Port may already have been open from a race; require our ssh alive.
                if proc.poll() is None:
                    ok = True
                    break
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
        targets: list[int] = []
        if self.paths.ssh_pid.is_file():
            try:
                old = int(self.paths.ssh_pid.read_text().strip())
            except ValueError:
                old = 0
            if old:
                targets.append(old)
            self.paths.ssh_pid.unlink(missing_ok=True)
        targets.extend(procutil.pids_listening_on(socks_port))
        # Match our dynamic forward even if LISTEN owner lookup failed.
        targets.extend(procutil.pids_cmdline_match(f"-D 127.0.0.1:{socks_port}"))
        targets.extend(procutil.pids_cmdline_match("connect_proxy.py"))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"ssh/orphan pid={pid} stopped")

    def stop_tunnel(self) -> None:
        try:
            self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"TUN stop: {exc}")
        disable_browser_proxy(self.paths.proxy_backup, log=self.log)
        disable_linux_env_proxy(self.paths.env_proxy_backup, log=self.log)
        self.stop_http_bridge()
        socks_port = int((self.config().get("ssh") or {}).get("local_socks_port") or 1080)
        self._reap_socks_orphans(socks_port)
        clear_git_proxy(self.paths.cli_env, self.paths.cli_ps1, log=self.log)
        clear_instead_of(self.log)
        self.paths.state_path.unlink(missing_ok=True)

    def enable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
        cfg = self.config()
        ssh = cfg.get("ssh") or {}
        socks_port = int(ssh.get("local_socks_port") or 1080)
        self.tun.start(
            socks_port=socks_port,
            corporate_proxy=resolve_corporate_proxy(cfg),
            ssh_host=str(ssh.get("host") or ""),
            sing_box_path=get_sing_box_path(cfg),
            elevate=get_tun_elevate(),
        )
        if persist:
            update_env_key(self.paths.env_path, "TUN", "1")
            self.log("TUN=1 записан в .env")

    def disable_tun(self, *, persist: bool = True) -> None:
        self.tun.stop()
        if persist:
            update_env_key(self.paths.env_path, "TUN", "0")
            self.log("TUN=0 записан в .env")

    def download_sing_box(self) -> None:
        self.reload_env()
        proxy_url = None
        http_port = get_http_bridge_port()
        if _port_open("127.0.0.1", http_port):
            proxy_url = f"http://127.0.0.1:{http_port}"
        path = self.tun.ensure_downloaded(proxy_url=proxy_url)
        # Persist path into config.json
        cfg = self.config()
        cfg.setdefault("tun", {})
        cfg["tun"]["sing_box_path"] = str(path)
        from desktop.config_io import save_config

        save_config(self.paths.config_path, cfg)
        self.log(f"tun.sing_box_path = {path}")

    def enable_relay(self) -> None:
        self.reload_env()
        cfg = self.config()
        secret = os.environ.get("OPS_CONTENT_SECRET") or ""
        base = enable_relay_git(cfg, secret, self.paths.state_path, log=self.log)
        state = {
            "mode": "relay",
            "base": base,
            "started": datetime.now(timezone.utc).isoformat(),
        }
        self.paths.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def disable_relay(self) -> None:
        cfg = None
        try:
            cfg = self.config()
        except FileNotFoundError:
            pass
        disable_relay_git(cfg, self.paths.cli_env, self.paths.cli_ps1, log=self.log)
        self.paths.state_path.unlink(missing_ok=True)

    def _maybe_autostart_tun(self) -> None:
        self.reload_env()
        if not get_tun_enabled():
            self.log("TUN=0 (.env) — системный TUN не поднимаем")
            return
        self.log("TUN=1 (.env) — поднимаем TUN поверх SOCKS")
        try:
            self.enable_tun(persist=False)
        except Exception as exc:  # noqa: BLE001
            # Tunnel already up — don't fail the whole `on` because of TUN
            self.log(f"TUN auto-start failed: {exc}")
            self.log("Туннель оставлен включённым. Нужен sing-box: download-sing-box / tools/")

    def enable(self) -> None:
        self.reload_env()
        mode = get_mode()
        self.log(f"MODE={mode} TUN={1 if get_tun_enabled() else 0}")
        if mode == "socks":
            self.start_tunnel()
            self._maybe_autostart_tun()
            return
        if mode == "vps":
            cfg = self.config()
            base = (cfg.get("worker_base_url") or "").strip()
            bad = re.search(
                r"YOUR_|ghfast|netlify|deno\.dev|pages\.dev|workers\.dev",
                base,
                re.I,
            )
            if base and not bad:
                self.enable_relay()
            else:
                self.start_tunnel()
                self._maybe_autostart_tun()
            return
        raise RuntimeError(f"Unknown MODE={mode} (use socks|vps)")

    def disable(self) -> None:
        self.reload_env()
        mode = get_mode()
        self.log(f"MODE={mode} -> off")
        # stop_tunnel already stops TUN process; keep TUN= flag in .env as user preference
        self.stop_tunnel()
        if mode == "vps":
            self.disable_relay()
        else:
            try:
                self.disable_relay()
            except Exception:  # noqa: BLE001
                pass

    def status(self, *, include_git: bool = True) -> dict[str, Any]:
        """Snapshot of tunnel state.

        include_git=False skips spawning `git config` (faster for GUI polling).
        """
        self.reload_env()
        info: dict[str, Any] = {
            "mode": get_mode(),
            "socks_scope": get_socks_scope() if get_mode() == "socks" else "",
            "git_http_proxy": "",
            "git_https_proxy": "",
            "ssh_running": False,
            "ssh_pid": None,
            "bridge_running": False,
            "bridge_pid": None,
            "http_port": get_http_bridge_port(),
            "corporate_proxy": "",
            "ssh_target": "",
            "worker_base_url": "",
            "proxy_bypass_n": 0,
            "state": None,
            "lines": [],
        }
        lines = info["lines"]
        info["tun_env"] = get_tun_enabled()
        lines.append(f"MODE (.env)       = {info['mode']}")
        if info["mode"] == "socks":
            lines.append(f"SOCKS_SCOPE       = {info['socks_scope']}")
        lines.append(f"TUN (.env)        = {1 if info['tun_env'] else 0}")
        if include_git:
            info["git_http_proxy"] = git_get("http.proxy")
            info["git_https_proxy"] = git_get("https.proxy")
            lines.append(f"git http.proxy  = {info['git_http_proxy']}")
            lines.append(f"git https.proxy = {info['git_https_proxy']}")

        if self.paths.ssh_pid.is_file():
            try:
                pid = int(self.paths.ssh_pid.read_text().strip())
            except ValueError:
                pid = 0
            info["ssh_pid"] = pid
            if pid and procutil.pid_alive(pid):
                info["ssh_running"] = True
                lines.append(f"ssh tunnel pid={pid} running")
            else:
                lines.append(f"ssh pid={pid} dead")

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

        info["tun_running"] = self.tun.running()
        info["tun_pid"] = self.tun.pid()
        if info["tun_running"]:
            lines.append(f"TUN (sing-box) pid={info['tun_pid']} running")
        else:
            lines.append("TUN (sing-box)   = off")

        if self.paths.state_path.is_file():
            try:
                info["state"] = json.loads(self.paths.state_path.read_text(encoding="utf-8-sig"))
                lines.append(f"state: {json.dumps(info['state'])}")
            except json.JSONDecodeError:
                lines.append("state: (invalid)")

        if self.paths.config_path.is_file():
            cfg = self.config()
            info["corporate_proxy"] = resolve_corporate_proxy(cfg)
            ssh = cfg.get("ssh") or {}
            info["ssh_target"] = f"{ssh.get('user')}@{ssh.get('host')}:{ssh.get('port')}"
            info["worker_base_url"] = str(cfg.get("worker_base_url") or "")
            info["proxy_bypass_n"] = len(cfg.get("proxy_bypass") or [])
            info["sing_box_path"] = get_sing_box_path(cfg)
            lines.append(f"corporate_proxy = {info['corporate_proxy']}")
            lines.append(f"ssh             = {info['ssh_target']}")
            if info["worker_base_url"]:
                lines.append(f"worker_base_url = {info['worker_base_url']}")
            lines.append(f"proxy_bypass    = {info['proxy_bypass_n']} entries")
            if info["sing_box_path"]:
                lines.append(f"sing_box_path   = {info['sing_box_path']}")

        info["active"] = bool(
            info["ssh_running"]
            or info["bridge_running"]
            or info.get("tun_running")
            or (info.get("state") or {}).get("mode") == "relay"
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
