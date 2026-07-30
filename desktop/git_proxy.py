"""git config + CLI env helpers for the local HTTP bridge / VPS relay."""

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
    clear_cli_env_proxy(cli_env, cli_ps1)
    log("git proxy cleared")


def enable_relay_git(cfg: dict, secret: str, state_path: Path, log: LogFn = _noop) -> str:
    base = (cfg.get("worker_base_url") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError(
            "Set worker_base_url in config.json to your VPS HTTPS relay, or use MODE=socks."
        )
    if re.search(
        r"ghfast\.top|ghproxy|kkgithub|netlify\.app|deno\.dev|pages\.dev|workers\.dev",
        base,
        re.I,
    ):
        raise RuntimeError("worker_base_url must be YOUR VPS, not a public SaaS mirror.")

    clear_git_proxy(
        state_path.parent / "cli.env",
        state_path.parent / "cli.ps1",
        log=_noop,
    )
    clear_instead_of(log)

    hosts = [
        ("https://github.com/", f"{base}/https/github.com/"),
        ("https://api.github.com/", f"{base}/https/api.github.com/"),
        ("https://codeload.github.com/", f"{base}/https/codeload.github.com/"),
        ("https://raw.githubusercontent.com/", f"{base}/https/raw.githubusercontent.com/"),
        ("https://objects.githubusercontent.com/", f"{base}/https/objects.githubusercontent.com/"),
    ]
    for frm, to in hosts:
        _git("config", "--global", f"url.{to}.insteadOf", frm)

    corp = cfg.get("corporate_proxy") or "192.0.2.10:3128"
    _git("config", "--global", "http.proxy", f"http://{corp}")
    _git("config", "--global", "https.proxy", f"http://{corp}")
    _git("config", "--global", "--unset-all", "http.extraHeader")
    _git("config", "--global", "http.extraHeader", "Accept-Encoding: identity")
    if secret:
        _git("config", "--global", f"http.{base}/.extraHeader", f"X-Ops-Content-Token: {secret}")
    else:
        log("OPS_CONTENT_SECRET empty — relay auth disabled on client")
    _git("config", "--global", "http.version", "HTTP/1.1")
    _git("config", "--global", "protocol.version", "1")
    _git("config", "--global", "http.postBuffer", "524288000")
    log(f"Relay ON -> {base}")
    return base


def disable_relay_git(cfg: dict | None, cli_env: Path, cli_ps1: Path, log: LogFn = _noop) -> None:
    base = ""
    if cfg and cfg.get("worker_base_url"):
        base = str(cfg["worker_base_url"]).strip().rstrip("/")
    clear_instead_of(log)
    clear_git_proxy(cli_env, cli_ps1, log=_noop)
    _git("config", "--global", "--unset-all", "http.extraHeader")
    if base:
        _git("config", "--global", "--unset-all", f"http.{base}/.extraHeader")
    _git("config", "--global", "--unset", "http.version")
    _git("config", "--global", "--unset", "protocol.version")
    _git("config", "--global", "--unset", "http.postBuffer")
    log("Relay OFF")
