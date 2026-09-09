"""git config + CLI env helpers for the local HTTP bridge."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

from desktop import procutil

LogFn = Callable[[str], None]

_BRIDGE_PORTS = (1088, 1080, 8877)


def _noop(msg: str) -> None:
    pass


def _git_exe() -> str:
    found = shutil.which("git")
    if found:
        return found
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        for path in (
            Path(pf) / "Git" / "cmd" / "git.exe",
            Path(pf) / "Git" / "bin" / "git.exe",
            Path(pf86) / "Git" / "cmd" / "git.exe",
            Path(local) / "Programs" / "Git" / "cmd" / "git.exe",
        ):
            if path.is_file():
                return str(path)
    return "git"


def _git(*args: str, check: bool = False):
    return procutil.run([_git_exe(), *args], check=check)


def git_get(key: str) -> str:
    r = _git("config", "--global", "--get", key)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def _is_local_bridge_proxy(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if not re.search(r"(127\.0\.0\.1|\[::1\]|localhost)", text, re.I):
        return False
    return any(re.search(rf":{port}(?:\D|$)", text) for port in _BRIDGE_PORTS)


def clear_instead_of(log: LogFn = _noop) -> None:
    r = _git("config", "--global", "--get-regexp", r"url\..*\.insteadof")
    if r.returncode != 0 or not r.stdout:
        return
    for line in r.stdout.splitlines():
        if re.search(r"ops-content|ergoms-vpn|ERGOMS|proxy-kill|/https/github", line):
            key = line.split(None, 1)[0]
            _git("config", "--global", "--unset-all", key)


def clear_cli_env_proxy(cli_env: Path, cli_ps1: Path) -> None:
    cli_env.unlink(missing_ok=True)
    cli_ps1.unlink(missing_ok=True)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        cur = os.environ.get(name, "")
        if cur and _is_local_bridge_proxy(cur):
            os.environ.pop(name, None)


def write_cli_env(http_port: int, cli_env: Path, cli_ps1: Path) -> None:
    proxy = f"http://127.0.0.1:{http_port}"
    noproxy = "localhost,127.0.0.1,::1"
    cli_env.parent.mkdir(parents=True, exist_ok=True)
    cli_env.write_text(
        "\n".join(
            [
                "# ERGOMS VPN CLI proxy (bash / Git Bash): source ./var/cli.env",
                f"export HTTP_PROXY={proxy}",
                f"export HTTPS_PROXY={proxy}",
                f"export http_proxy={proxy}",
                f"export https_proxy={proxy}",
                f"export ALL_PROXY={proxy}",
                f"export NO_PROXY={noproxy}",
                f"export no_proxy={noproxy}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cli_ps1.write_text(
        "\r\n".join(
            [
                "# ERGOMS VPN CLI proxy (PowerShell): . .\\var\\cli.ps1",
                f"$env:HTTP_PROXY = '{proxy}'",
                f"$env:HTTPS_PROXY = '{proxy}'",
                f"$env:http_proxy = '{proxy}'",
                f"$env:https_proxy = '{proxy}'",
                f"$env:ALL_PROXY = '{proxy}'",
                f"$env:NO_PROXY = '{noproxy}'",
                f"$env:no_proxy = '{noproxy}'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        os.environ[name] = proxy
    os.environ["NO_PROXY"] = noproxy
    os.environ["no_proxy"] = noproxy


def _backup_git_proxy(backup_path: Path) -> None:
    if backup_path.is_file():
        return
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(
        json.dumps(
            {
                "http.proxy": git_get("http.proxy"),
                "https.proxy": git_get("https.proxy"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _restore_git_proxy(backup_path: Path) -> bool:
    if not backup_path.is_file():
        return False
    try:
        data = json.loads(backup_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        backup_path.unlink(missing_ok=True)
        return False
    if not isinstance(data, dict):
        backup_path.unlink(missing_ok=True)
        return False
    for key in ("http.proxy", "https.proxy"):
        prev = str(data.get(key) or "").strip()
        if prev and not _is_local_bridge_proxy(prev):
            _git("config", "--global", key, prev)
        else:
            _git("config", "--global", "--unset-all", key)
    backup_path.unlink(missing_ok=True)
    return True


def _unset_bridge_keys() -> None:
    for key in ("http.proxy", "https.proxy"):
        cur = git_get(key)
        if not cur or _is_local_bridge_proxy(cur):
            _git("config", "--global", "--unset-all", key)
    r = _git("config", "--global", "--get-regexp", r"^(http|https)\..*proxy$")
    if r.returncode == 0 and r.stdout:
        for line in r.stdout.splitlines():
            key = line.split(None, 1)[0]
            val = line.split(None, 1)[1] if " " in line else ""
            if key and _is_local_bridge_proxy(val):
                _git("config", "--global", "--unset-all", key)


def set_git_http_proxy(
    proxy: str,
    log: LogFn = _noop,
    *,
    backup_path: Path | None = None,
) -> None:
    clear_instead_of(log)
    if backup_path is not None:
        _backup_git_proxy(backup_path)
    _git("config", "--global", "http.proxy", proxy)
    _git("config", "--global", "https.proxy", proxy)
    _git("config", "--global", "credential.https://github.com.provider", "generic")
    log(f"git http(s).proxy = {proxy}")


def clear_git_proxy(
    cli_env: Path,
    cli_ps1: Path,
    log: LogFn = _noop,
    *,
    backup_path: Path | None = None,
) -> None:
    restored = False
    if backup_path is not None:
        restored = _restore_git_proxy(backup_path)
    _unset_bridge_keys()
    clear_instead_of(log)
    _git("config", "--global", "--unset-all", "http.extraHeader")
    r = _git("config", "--global", "--get-regexp", r"^http\..*\.extraheader$")
    if r.returncode == 0 and r.stdout:
        for line in r.stdout.splitlines():
            key = line.split(None, 1)[0]
            if key:
                _git("config", "--global", "--unset-all", key)
    _git("config", "--global", "--unset", "http.version")
    _git("config", "--global", "--unset", "protocol.version")
    _git("config", "--global", "--unset", "http.postBuffer")
    clear_cli_env_proxy(cli_env, cli_ps1)
    log("git proxy restored" if restored else "git proxy cleared")


def clear_stale_git_proxy(
    cli_env: Path,
    cli_ps1: Path,
    log: LogFn = _noop,
    *,
    backup_path: Path | None = None,
) -> bool:
    """Remove our leftover git/CLI proxy when the VPN is not using it."""
    proxy = git_get("http.proxy") or git_get("https.proxy")
    has_cli = cli_env.is_file() or cli_ps1.is_file()
    has_backup = bool(backup_path and backup_path.is_file())
    if not _is_local_bridge_proxy(proxy) and not has_cli and not has_backup:
        return False
    clear_git_proxy(cli_env, cli_ps1, log=log, backup_path=backup_path)
    if _is_local_bridge_proxy(proxy):
        log(f"снят git proxy ({proxy})")
    return True
