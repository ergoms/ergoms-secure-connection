"""Write a portable OpenSSH include + optional ssh-copy-id for lab hosts.

On a new PC: copy config.json (or decrypt), put the same private key in
creds/ or ~/.ssh/server-vps, run `python -m desktop ssh-setup`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from desktop.client_util import which
from desktop.config_io import (
    get_local_socks_port,
    get_reverse_ssh,
    get_server_host,
    load_config,
    save_config,
)
from desktop.logutil import noop
from desktop.paths import Paths, is_frozen, resolve_ssh_identity, self_command

LogFn = Callable[[str], None]

INCLUDE_NAME = "ergoms-secure-connection.conf"
WRAPPER_WIN = "ergoms-connect-socks.cmd"
WRAPPER_UNIX = "ergoms-connect-socks"
INCLUDE_BEGIN = "# BEGIN ERGOMS SECURE CONNECTION"
INCLUDE_END = "# END ERGOMS SECURE CONNECTION"
MANAGED_MARK = "# ERGOMS SECURE CONNECTION (ssh-setup)"
MANAGED_ALIASES = {
    "vps-server",
    "server-vps",
    "vps-server-direct",
    "bstu-server-laboratory-proxy-1",
    "bstu-server-laboratory-1",
    "lab",
}


def ssh_exe() -> str:
    if sys.platform == "win32":
        windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        native = Path(windir) / "System32" / "OpenSSH" / "ssh.exe"
        if native.is_file():
            return str(native)
    return which("ssh") or "ssh"


def ssh_keygen_exe() -> str:
    exe = ssh_exe()
    if exe.lower().endswith("ssh.exe"):
        cand = str(Path(exe).with_name("ssh-keygen.exe"))
        if Path(cand).is_file():
            return cand
    return which("ssh-keygen") or "ssh-keygen"


def posix_path(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def ssh_identity_line(identity: Path) -> str:
    try:
        home_ssh = (Path.home() / ".ssh").resolve()
        resolved = identity.expanduser().resolve()
        if resolved.parent == home_ssh:
            return f"~/.ssh/{resolved.name}"
    except OSError:
        pass
    return posix_path(identity)


def default_hosts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"host": "vps-server-direct", "via_socks": False},
        {"host": "vps-server server-vps", "via_socks": True},
        {
            "host": "bstu-server-laboratory-proxy-1 bstu-server-laboratory-1 lab",
            "hostname": "127.0.0.1",
            "port": 2222,
            "user": "dohao",
            "proxy_jump": "vps-server",
            "copy_id": True,
        },
    ]


def client_ssh_block(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("client_ssh")
    if not isinstance(raw, dict):
        raw = {}
    hosts = raw.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        hosts = default_hosts(cfg)
    return {
        "identity_file": str(raw.get("identity_file") or "").strip(),
        "hosts": hosts,
    }


def persist_client_ssh(paths: Paths, cfg: dict[str, Any]) -> dict[str, Any]:
    block = client_ssh_block(cfg)
    cfg = dict(cfg)
    cfg["client_ssh"] = block
    save_config(paths.config_path, cfg)
    return cfg


def resolve_identity(cfg: dict[str, Any], paths: Paths) -> Path:
    block = client_ssh_block(cfg)
    rev = get_reverse_ssh(cfg)
    candidates: list[Path] = []
    for raw in (
        block.get("identity_file"),
        rev.get("identity_file"),
        paths.creds_dir / "server-vps",
        Path.home() / ".ssh" / "server-vps",
        Path.home() / ".ssh" / "id_ed25519",
        Path.home() / ".ssh" / "id_rsa",
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = paths.root / path
        candidates.append(path)
    auto = resolve_ssh_identity(paths.creds_dir)
    if auto:
        candidates.append(Path(auto))
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    raise RuntimeError(
        "Нет SSH-ключа. Положите приватный ключ в creds/server-vps "
        "или ~/.ssh/server-vps (тот же файл на каждом ПК)."
    )


def ensure_public_key(identity: Path) -> Path:
    pub = identity.with_name(identity.name + ".pub")
    if pub.is_file():
        return pub
    r = subprocess.run(
        [ssh_keygen_exe(), "-y", "-f", str(identity)],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        raise RuntimeError(f"ssh-keygen -y не смог прочитать {identity}: {r.stderr.strip()}")
    pub.write_text(r.stdout.strip() + "\n", encoding="utf-8")
    try:
        os.chmod(pub, 0o644)
    except OSError:
        pass
    return pub


def _console_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() in ("python.exe", "python3.exe", "python3") and exe.is_file():
        if "WindowsApps" not in str(exe):
            return str(exe.resolve())
    if sys.platform == "win32" and exe.name.lower() == "pythonw.exe":
        py = exe.with_name("python.exe")
        if py.is_file():
            return str(py.resolve())
    for name in ("python3", "python"):
        found = which(name)
        if found and "WindowsApps" not in found:
            return found
    return str(exe)


def write_socks_wrapper(ssh_dir: Path, paths: Paths, socks_port: int) -> Path:
    ssh_dir.mkdir(parents=True, exist_ok=True)
    socks = f"127.0.0.1:{int(socks_port)}"
    if is_frozen():
        cmd = self_command()
        inner = " ".join(f'"{c}"' if " " in c else c for c in [*cmd, "connect-socks"])
        if sys.platform == "win32":
            path = ssh_dir / WRAPPER_WIN
            path.write_text(
                "\r\n".join(
                    [
                        "@echo off",
                        f"set ERGOMS_SC_SOCKS={socks}",
                        f"{inner} %1 %2",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        else:
            path = ssh_dir / WRAPPER_UNIX
            path.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        f'export ERGOMS_SC_SOCKS="{socks}"',
                        f"exec {inner} \"$1\" \"$2\"",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            os.chmod(path, 0o755)
        return path

    py = _console_python()
    script = paths.root / "lib" / "connect_socks.py"
    if not script.is_file():
        raise RuntimeError(f"нет {script}")
    if sys.platform == "win32":
        path = ssh_dir / WRAPPER_WIN
        path.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    f"set ERGOMS_SC_SOCKS={socks}",
                    f'"{py}" -u "{script}" %1 %2',
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        path = ssh_dir / WRAPPER_UNIX
        path.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    f'export ERGOMS_SC_SOCKS="{socks}"',
                    f'exec "{py}" -u "{script}" "$1" "$2"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        os.chmod(path, 0o755)
    return path


def _aliases(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw]
    else:
        parts = str(raw or "").split()
    return [p for p in parts if p]


def render_include(
    *,
    cfg: dict[str, Any],
    identity: Path,
    wrapper: Path,
) -> str:
    rev = get_reverse_ssh(cfg)
    server = get_server_host(cfg)
    vps_user = str(rev.get("vps_user") or "root").strip() or "root"
    vps_port = int(rev.get("vps_port") or 22)
    ident = ssh_identity_line(identity)
    wrap = posix_path(wrapper)
    lines = [
        MANAGED_MARK,
        "",
    ]
    for raw in client_ssh_block(cfg)["hosts"]:
        if not isinstance(raw, dict):
            continue
        names = _aliases(raw.get("host") or raw.get("aliases"))
        if not names:
            continue
        hostname = str(raw.get("hostname") or raw.get("host_name") or "").strip() or server
        user = str(raw.get("user") or "").strip() or vps_user
        port = int(raw.get("port") or vps_port)
        via_socks = bool(raw.get("via_socks"))
        jump = str(raw.get("proxy_jump") or raw.get("proxyjump") or "").strip()
        host_ident = str(raw.get("identity_file") or "").strip()
        ident_line = ssh_identity_line(Path(host_ident).expanduser()) if host_ident else ident
        lines.append(f"Host {' '.join(names)}")
        lines.append(f"  HostName {hostname}")
        if port != 22 or jump:
            lines.append(f"  Port {port}")
        lines.append(f"  User {user}")
        lines.append(f"  IdentityFile {ident_line}")
        lines.append("  IdentitiesOnly yes")
        if jump:
            lines.append(f"  ProxyJump {jump}")
        elif via_socks:
            lines.append(f"  ProxyCommand {wrap} %h %p")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _strip_managed_hosts(text: str) -> str:
    out: list[str] = []
    skip = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("host "):
            names = {n.lower() for n in stripped.split()[1:]}
            skip = bool(names & MANAGED_ALIASES)
        if skip:
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out).rstrip() + ("\n" if out else "")


def _strip_include_block(text: str) -> str:
    out: list[str] = []
    skip = False
    for line in text.splitlines():
        if line.strip() == INCLUDE_BEGIN:
            skip = True
            continue
        if line.strip() == INCLUDE_END:
            skip = False
            continue
        if skip:
            continue
        if line.strip() in (
            f"Include {INCLUDE_NAME}",
            f"Include ~/.ssh/{INCLUDE_NAME}",
        ):
            continue
        out.append(line)
    return "\n".join(out).rstrip() + ("\n" if out else "")


def write_user_config(ssh_dir: Path, managed: str) -> Path:
    config = ssh_dir / "config"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    existing = config.read_text(encoding="utf-8") if config.is_file() else ""
    body = _strip_managed_hosts(_strip_include_block(existing))
    cleaned: list[str] = []
    for line in body.splitlines():
        if line.strip() in {MANAGED_MARK, INCLUDE_BEGIN, INCLUDE_END}:
            continue
        cleaned.append(line)
    body = "\n".join(cleaned).strip()
    chunks = [part for part in (body, managed.strip()) if part]
    config.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    extra = ssh_dir / INCLUDE_NAME
    extra.unlink(missing_ok=True)
    return config


def copy_id_targets(cfg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for raw in client_ssh_block(cfg)["hosts"]:
        if not isinstance(raw, dict):
            continue
        names = _aliases(raw.get("host") or raw.get("aliases"))
        if not names:
            continue
        flagged = raw.get("copy_id")
        jump = str(raw.get("proxy_jump") or "").strip()
        if flagged is False:
            continue
        if flagged or jump:
            out.append(names[0])
    return out


def _copy_id_remote(pubkey: str) -> str:
    # Single-quoted remote script; pubkey is OpenSSH one-line and has no quotes.
    return (
        "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
        f"grep -qxF '{pubkey}' ~/.ssh/authorized_keys || "
        f"echo '{pubkey}' >> ~/.ssh/authorized_keys"
    )


def copy_id_args(host: str, pubkey: str) -> list[str]:
    return [
        ssh_exe(),
        "-o",
        "PreferredAuthentications=password,keyboard-interactive",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "NumberOfPasswordPrompts=3",
        host,
        _copy_id_remote(pubkey),
    ]


def write_copy_id_cmd(ssh_dir: Path, host: str, pubkey: str) -> Path:
    args = copy_id_args(host, pubkey)
    ssh_dir.mkdir(parents=True, exist_ok=True)
    path = ssh_dir / "ergoms-copy-id.cmd"
    escaped = " ".join(f'"{a}"' for a in args)
    path.write_text(
        "\r\n".join(
            [
                "@echo off",
                "echo ERGOMS ssh-setup: enter the password for the lab host, then close the window.",
                escaped,
                "echo exit %ERRORLEVEL%",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def run_copy_id(host: str, pubkey: str, log: LogFn = noop) -> int:
    args = copy_id_args(host, pubkey)
    log(f"ssh-copy-id → {host} (пароль dohao)")
    if sys.stdin.isatty():
        return int(subprocess.call(args))
    script = write_copy_id_cmd(Path.home() / ".ssh", host, pubkey)
    log(f"не TTY — запусти сам: {script}")
    return 0


def write_setup(
    paths: Paths,
    log: LogFn = noop,
    *,
    persist: bool = False,
) -> dict[str, Path]:
    cfg = load_config(paths.config_path, force=True)
    if persist:
        cfg = persist_client_ssh(paths, cfg)
    identity = resolve_identity(cfg, paths)
    pub = ensure_public_key(identity)
    ssh_dir = Path.home() / ".ssh"
    wrapper = write_socks_wrapper(ssh_dir, paths, get_local_socks_port(cfg))
    managed = render_include(cfg=cfg, identity=identity, wrapper=wrapper)
    user_cfg = write_user_config(ssh_dir, managed)
    log(f"SSH config → {user_cfg}")
    log(f"ключ → {identity}")
    log(f"pubkey → {pub}")
    return {"include": user_cfg, "config": user_cfg, "identity": identity, "pub": pub, "wrapper": wrapper}


def setup_and_copy_id(
    paths: Paths,
    log: LogFn = noop,
    *,
    copy_id: bool = True,
    hosts: list[str] | None = None,
) -> int:
    written = write_setup(paths, log=log)
    if not copy_id:
        log("На другом ПК: тот же config.json, тот же ключ, снова ssh-setup.")
        return 0
    cfg = load_config(paths.config_path, force=True)
    targets = hosts or copy_id_targets(cfg)
    if not targets:
        log("нет хостов для copy-id")
        return 0
    pub = written["pub"].read_text(encoding="utf-8").strip().splitlines()[0].strip()
    script = write_copy_id_cmd(Path.home() / ".ssh", targets[0], pub)
    log(f"copy-id скрипт → {script}")
    if not sys.stdin.isatty():
        log("Запусти этот файл и введи пароль dohao. Потом: ssh bstu-server-laboratory-proxy-1")
        return 0
    rc = 0
    for host in targets:
        code = run_copy_id(host, pub, log=log)
        if code != 0:
            log(f"copy-id {host} exit={code}")
            rc = code
        else:
            log(f"copy-id {host} OK")
    return rc
