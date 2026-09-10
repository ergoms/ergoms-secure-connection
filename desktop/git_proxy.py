"""git config + CLI env helpers for the local HTTP bridge."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from desktop import procutil

LogFn = Callable[[str], None]

_BRIDGE_PORTS = (1088, 1080, 8877)


def _noop(msg: str) -> None:
    pass


def _git_exe() -> str:
    extra: list[Path] = []
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        extra = [
            Path(pf) / "Git" / "cmd" / "git.exe",
            Path(pf) / "Git" / "bin" / "git.exe",
            Path(pf86) / "Git" / "cmd" / "git.exe",
            Path(local) / "Programs" / "Git" / "cmd" / "git.exe",
        ]
    found = procutil.which_exe("git", extra)
    return found or "git"


def _git(*args: str, check: bool = False):
    return procutil.run([_git_exe(), *args], check=check, timeout=10)


def _global_gitconfig_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("GIT_CONFIG_GLOBAL")
    if override:
        paths.append(Path(override))
    paths.append(Path.home() / ".gitconfig")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        paths.append(Path(xdg) / "git" / "config")
    else:
        paths.append(Path.home() / ".config" / "git" / "config")
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _gitconfig_looks_dirty() -> bool:
    """True if ~/.gitconfig still has our proxy / insteadOf leftovers."""
    for path in _global_gitconfig_paths():
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if not text:
            continue
        low = text.lower()
        if any(f":{port}" in low for port in _BRIDGE_PORTS) and (
            "127.0.0.1" in low or "localhost" in low or "[::1]" in low
        ):
            return True
        if re.search(
            r"ops-content|ergoms-vpn|ergoms-secure-connection|proxy-kill|/https/github",
            text,
            re.I,
        ):
            return True
    return False


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
        if re.search(r"ops-content|ergoms-vpn|ergoms-secure-connection|ERGOMS|proxy-kill|/https/github", line):
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
                "# ERGOMS SECURE CONNECTION CLI proxy (bash / Git Bash): source ./var/cli.env",
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
                "# ERGOMS SECURE CONNECTION CLI proxy (PowerShell): . .\\var\\cli.ps1",
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


def _git_config_list() -> list[tuple[str, str]]:
    r = _git("config", "--global", "--list")
    if r.returncode != 0 or not r.stdout:
        return []
    pairs: list[tuple[str, str]] = []
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            pairs.append((key, val))
    return pairs


def _backup_git_proxy(backup_path: Path) -> None:
    if backup_path.is_file():
        return
    values = {"http.proxy": "", "https.proxy": ""}
    for key, val in _git_config_list():
        low = key.lower()
        if low in values:
            values[low] = val.strip()
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(values, indent=2), encoding="utf-8")


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


def _keys_to_clear(pairs: list[tuple[str, str]], *, keep_restored_proxy: bool) -> list[str]:
    del keep_restored_proxy
    extra = (
        "http.extraheader",
        "http.version",
        "protocol.version",
        "http.postbuffer",
    )
    instead_re = re.compile(
        r"ops-content|ergoms-vpn|ergoms-secure-connection|ERGOMS|proxy-kill|/https/github"
    )
    to_unset: list[str] = []
    seen: set[str] = set()
    for key, val in pairs:
        low = key.lower()
        drop = False
        if low in ("http.proxy", "https.proxy"):
            drop = _is_local_bridge_proxy(val)
        elif "proxy" in low and _is_local_bridge_proxy(val):
            drop = True
        elif "insteadof" in low and instead_re.search(f"{key} {val}"):
            drop = True
        elif low in extra or re.match(r"^http\..*\.extraheader$", low):
            drop = True
        if drop and key not in seen:
            seen.add(key)
            to_unset.append(key)
    return to_unset


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
    if backup_path is not None and backup_path.is_file():
        restored = _restore_git_proxy(backup_path)
    keys: list[str] = []
    # TUN mode never writes git http.proxy — skip git.exe unless leftovers exist.
    if restored or _gitconfig_looks_dirty():
        pairs = _git_config_list()
        keys = _keys_to_clear(pairs, keep_restored_proxy=restored)
        for key in keys:
            _git("config", "--global", "--unset-all", key)
    had_cli = cli_env.is_file() or cli_ps1.is_file()
    clear_cli_env_proxy(cli_env, cli_ps1)
    if restored or keys or had_cli:
        log("git proxy restored" if restored else "git proxy cleared")


def clear_stale_git_proxy(
    cli_env: Path,
    cli_ps1: Path,
    log: LogFn = _noop,
    *,
    backup_path: Path | None = None,
) -> bool:
    """Remove our leftover git/CLI proxy when the VPN is not using it."""
    has_cli = cli_env.is_file() or cli_ps1.is_file()
    has_backup = bool(backup_path and backup_path.is_file())
    dirty = _gitconfig_looks_dirty()
    if not dirty and not has_cli and not has_backup:
        return False
    clear_git_proxy(cli_env, cli_ps1, log=log, backup_path=backup_path)
    if dirty:
        log("снят git proxy")
    return True
