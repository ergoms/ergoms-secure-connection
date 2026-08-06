"""git config + CLI env helpers for the local HTTP bridge."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from desktop import procutil

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def _git(*args: str, check: bool = False):
    return procutil.run(["git", *args], check=check)


def git_get(key: str) -> str:
    r = _git("config", "--global", "--get", key)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def clear_instead_of(log: LogFn = _noop) -> None:
    r = _git("config", "--global", "--get-regexp", r"url\..*\.insteadof")
    if r.returncode != 0 or not r.stdout:
        return
    for line in r.stdout.splitlines():
        if re.search(r"ops-content|proxy-kill|/https/github", line):
            key = line.split(None, 1)[0]
            _git("config", "--global", "--unset-all", key)


def clear_cli_env_proxy(cli_env: Path, cli_ps1: Path) -> None:
    cli_env.unlink(missing_ok=True)
    cli_ps1.unlink(missing_ok=True)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        cur = os.environ.get(name, "")
        if cur and re.search(r"127\.0\.0\.1:(1088|8877)", cur):
            os.environ.pop(name, None)


def write_cli_env(http_port: int, cli_env: Path, cli_ps1: Path) -> None:
    proxy = f"http://127.0.0.1:{http_port}"
    noproxy = "localhost,127.0.0.1,::1"
    cli_env.parent.mkdir(parents=True, exist_ok=True)
    cli_env.write_text(
        "\n".join(
            [
                "# ops-content CLI proxy (bash / Git Bash): source ./var/cli.env",
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
                "# ops-content CLI proxy (PowerShell): . .\\var\\cli.ps1",
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


def set_git_http_proxy(proxy: str, log: LogFn = _noop) -> None:
    clear_instead_of(log)
    _git("config", "--global", "http.proxy", proxy)
    _git("config", "--global", "https.proxy", proxy)
    _git("config", "--global", "credential.https://github.com.provider", "generic")
    log(f"git http(s).proxy = {proxy}")


def clear_git_proxy(cli_env: Path, cli_ps1: Path, log: LogFn = _noop) -> None:
    _git("config", "--global", "--unset", "http.proxy")
    _git("config", "--global", "--unset", "https.proxy")
    clear_instead_of(log)
    _git("config", "--global", "--unset-all", "http.extraHeader")
    # Leftovers from old HTTPS git-relay (url.*.insteadOf / http.<url>.extraHeader)
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
    log("git proxy cleared")
