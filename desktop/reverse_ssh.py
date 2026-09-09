"""Reverse SSH: client:22 → VPS loopback via local SOCKS (VLESS), not office :22."""

from __future__ import annotations

import getpass
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from desktop import procutil
from desktop.config_io import get_local_socks_port, get_reverse_ssh, get_server_host
from desktop.paths import Paths, bundle_dir, is_frozen, resolve_ssh_identity, self_command

LogFn = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def _which(name: str) -> str | None:
    return shutil.which(name)


def _ensure_script(paths: Paths, name: str) -> Path:
    dst = paths.root / "lib" / name
    if dst.is_file():
        return dst
    src = bundle_dir() / "lib" / name
    if not src.is_file():
        raise RuntimeError(f"{name} not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def find_pythonw() -> str | None:
    if sys.platform != "win32":
        return _which("python3") or _which("python")
    candidates: list[Path] = []
    exe = Path(sys.executable)
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
        if c.name.lower() == "pythonw.exe":
            return str(c.resolve())
    for c in candidates:
        if c.is_file() and "WindowsApps" not in str(c):
            return str(c.resolve())
    return None


def socks_proxy_command(paths: Paths) -> str:
    """OpenSSH ProxyCommand that dials through local SOCKS :1080."""
    if is_frozen():
        exe = str(Path(sys.executable).resolve())
        return f'"{exe}" connect-socks %h %p'
    py = find_pythonw()
    if not py:
        raise RuntimeError("Python not found for SOCKS ProxyCommand")
    script = _ensure_script(paths, "connect_socks.py")
    _ensure_script(paths, "connect_proxy.py")
    return f'"{py}" "{script}" %h %p'


def resolve_identity(cfg: dict[str, Any], paths: Paths) -> str:
    rev = get_reverse_ssh(cfg)
    explicit = str(rev.get("identity_file") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = paths.root / path
        if not path.is_file():
            raise RuntimeError(f"reverse_ssh.identity_file not found: {path}")
        return str(path.resolve())
    auto = resolve_ssh_identity(paths.creds_dir)
    if not auto:
        raise RuntimeError(
            "Нет SSH-ключа для входа на VPS. Положите приватный ключ в creds/ "
            "(тот же, что в /root/.ssh/authorized_keys на сервере) "
            "или укажите reverse_ssh.identity_file"
        )
    return auto


def connect_hint(cfg: dict[str, Any], *, local_user: str | None = None) -> str:
    rev = get_reverse_ssh(cfg)
    listen = int(rev.get("listen_port") or 2222)
    user = local_user or getpass.getuser()
    return (
        f"ssh -p {listen} {user}@127.0.0.1\n"
        f"# или: bash modes/vps/ssh-to-client.sh {listen} {user}"
    )


def write_hint(paths: Paths, cfg: dict[str, Any]) -> Path:
    text = (
        "# Reverse SSH слушает на ЭТОМ VPS (loopback).\n"
        "# С офиса на :22 ходить нельзя — Squid рвёт соединение.\n"
        "#\n"
        f"{connect_hint(cfg)}"
        "\n"
        "# На клиенте должен работать OpenSSH Server (sshd).\n"
        "# На VPS нужен ключ пользователя в authorized_keys клиента.\n"
    )
    path = paths.var_dir / "reverse-ssh.txt"
    path.write_text(text, encoding="utf-8")
    return path


class ReverseSshManager:
    def __init__(self, paths: Paths, log: LogFn = _noop) -> None:
        self.paths = paths
        self.log = log
        self.pid_path = paths.var_dir / "reverse-ssh.pid"
        self.log_path = paths.logs_dir / "reverse-ssh.log"

    def pid(self) -> int | None:
        if not self.pid_path.is_file():
            return None
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None
        if pid and procutil.pid_alive(pid):
            return pid
        return None

    def running(self) -> bool:
        return self.pid() is not None

    def stop(self) -> None:
        targets: list[int] = []
        old = self.pid()
        if old:
            targets.append(old)
        self.pid_path.unlink(missing_ok=True)
        targets.extend(procutil.pids_cmdline_match("connect_socks.py", cache=False))
        targets.extend(procutil.pids_cmdline_match("connect-socks", cache=False))
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"reverse-ssh pid={pid} stopped")

    def start(self, cfg: dict[str, Any]) -> None:
        rev = get_reverse_ssh(cfg)
        host = get_server_host(cfg)
        if not host or "YOUR_VPS" in host:
            raise RuntimeError("Set real server.host in config.json")
        socks = get_local_socks_port(cfg)
        if not procutil.wait_port_open("127.0.0.1", socks, timeout=2.0):
            raise RuntimeError(f"SOCKS 127.0.0.1:{socks} down — сначала ergoms-vpn on")

        ssh = _which("ssh")
        if not ssh:
            raise RuntimeError("ssh не найден в PATH (Windows: компонент OpenSSH Client)")

        ident = resolve_identity(cfg, self.paths)
        listen = int(rev.get("listen_port") or 2222)
        local_port = int(rev.get("local_port") or 22)
        vps_port = int(rev.get("vps_port") or 22)
        vps_user = str(rev.get("vps_user") or "root").strip() or "root"

        if self.running():
            self.log(f"reverse-ssh already running (pid={self.pid()}) :{listen}")
            write_hint(self.paths, cfg)
            return

        if not procutil.wait_port_open("127.0.0.1", local_port, timeout=0.4):
            self.log(
                f"WARN: на клиенте не слушает sshd :{local_port}. "
                "Windows: Параметры → Приложения → Доп. компоненты → OpenSSH Server, "
                "затем Start-Service sshd. Linux: systemctl enable --now ssh"
            )

        try:
            os.chmod(ident, 0o600)
        except OSError:
            pass

        proxy = socks_proxy_command(self.paths)
        kh = str(self.paths.known_hosts).replace("\\", "/")
        gkh = "NUL" if sys.platform == "win32" else "/dev/null"
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        self.paths.var_dir.mkdir(parents=True, exist_ok=True)

        args = [
            ssh,
            "-N",
            "-R",
            f"127.0.0.1:{listen}:127.0.0.1:{local_port}",
            "-p",
            str(vps_port),
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
            "IdentitiesOnly=yes",
            "-i",
            ident,
            "-o",
            f"ProxyCommand={proxy}",
            f"{vps_user}@{host}",
        ]

        env = os.environ.copy()
        env["ERGOMS_VPN_SOCKS"] = f"127.0.0.1:{socks}"
        env["OPS_CONTENT_SOCKS"] = f"127.0.0.1:{socks}"
        root = str(self.paths.root)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"

        log_fh = self.log_path.open("ab")
        try:
            proc = procutil.popen(
                args,
                env=env,
                cwd=root,
                stdout=log_fh,
                stderr=log_fh,
                detached=True,
            )
        finally:
            log_fh.close()

        self.pid_path.write_text(str(proc.pid), encoding="utf-8")
        time.sleep(1.2)
        if proc.poll() is not None or not procutil.pid_alive(proc.pid):
            self.pid_path.unlink(missing_ok=True)
            tail = ""
            if self.log_path.is_file():
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-800:]
            raise RuntimeError(
                "reverse-ssh сразу завершился. Ключ должен пускать на VPS :22 "
                f"пользователя {vps_user}. Лог: {self.log_path}\n{tail}"
            )

        hint = write_hint(self.paths, cfg)
        self.log(
            f"reverse-ssh pid={proc.pid}  VPS:127.0.0.1:{listen} → клиент:{local_port}"
        )
        self.log(f"с VPS: {connect_hint(cfg).splitlines()[0]}")
        self.log(f"подсказка: {hint}")
