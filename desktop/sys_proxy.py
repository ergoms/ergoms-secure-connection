"""OS system proxy (PAC): Windows Internet Settings or GNOME gsettings.

Linux SOCKS: also override /etc/environment + profile.d so curl/ergoms
use the local HTTP bridge instead of a corporate Squid from pam_env.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from desktop import procutil

LogFn = Callable[[str], None]

_LINUX_PROFILE_D = Path("/etc/profile.d/Z50-ops-content-proxy.sh")
_LINUX_ENVIRONMENT = Path("/etc/environment")
_ENV_PROXY_KEYS = (
    "http_proxy",
    "https_proxy",
    "ftp_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


def _noop(msg: str) -> None:
    pass


def _parse_environment(text: str) -> tuple[list[str], dict[str, str]]:
    """Return (all_lines, proxy_key→raw_value) from /etc/environment-style file."""
    proxy_vals: dict[str, str] = {}
    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        if key in _ENV_PROXY_KEYS:
            proxy_vals[key] = raw.strip()
    return lines, proxy_vals


def _render_environment(lines: list[str], overrides: dict[str, str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in overrides:
                out.append(f'{key}="{overrides[key]}"')
                seen.add(key)
                continue
        out.append(line)
    for key, value in overrides.items():
        if key not in seen:
            out.append(f'{key}="{value}"')
    return "\n".join(out) + ("\n" if out else "")


def enable_linux_env_proxy(
    http_port: int,
    backup_path: Path,
    log: LogFn = _noop,
) -> None:
    """Point system CLI proxy at local HTTP bridge (overrides corporate Squid)."""
    if sys.platform == "win32":
        return

    proxy = f"http://127.0.0.1:{http_port}"
    noproxy = "localhost,127.0.0.1,::1"
    profile_body = "\n".join(
        [
            "# Managed by ops-content — do not edit by hand",
            f'export http_proxy="{proxy}"',
            f'export https_proxy="{proxy}"',
            f'export HTTP_PROXY="{proxy}"',
            f'export HTTPS_PROXY="{proxy}"',
            f'export ALL_PROXY="{proxy}"',
            f'export ftp_proxy="{proxy}"',
            f'export NO_PROXY="{noproxy}"',
            f'export no_proxy="{noproxy}"',
            "",
        ]
    )

    try:
        _LINUX_PROFILE_D.write_text(profile_body, encoding="utf-8")
        _LINUX_PROFILE_D.chmod(0o644)
    except OSError as exc:
        log(
            f"Нет прав записать {_LINUX_PROFILE_D} ({exc}). "
            "Запустите on с sudo или: source ./var/cli.env"
        )
        return

    overrides = {
        "http_proxy": proxy,
        "https_proxy": proxy,
        "ftp_proxy": proxy,
        "NO_PROXY": noproxy,
        "no_proxy": noproxy,
    }

    if _LINUX_ENVIRONMENT.is_file():
        try:
            original = _LINUX_ENVIRONMENT.read_text(encoding="utf-8")
        except OSError as exc:
            log(f"Не удалось прочитать {_LINUX_ENVIRONMENT}: {exc}")
            log(f"profile.d proxy → {proxy}")
            return

        lines, previous = _parse_environment(original)
        if not backup_path.is_file():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(
                json.dumps({"proxy": previous, "had_file": True}, indent=2),
                encoding="utf-8",
            )
        try:
            _LINUX_ENVIRONMENT.write_text(
                _render_environment(lines, overrides),
                encoding="utf-8",
            )
        except OSError as exc:
            log(f"Не удалось обновить {_LINUX_ENVIRONMENT}: {exc}")
            log(f"profile.d proxy → {proxy} (переоткройте shell / bash -lc)")
            return
    else:
        if not backup_path.is_file():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(
                json.dumps({"proxy": {}, "had_file": False}, indent=2),
                encoding="utf-8",
            )
        try:
            _LINUX_ENVIRONMENT.write_text(
                _render_environment([], overrides),
                encoding="utf-8",
            )
        except OSError as exc:
            log(f"Не удалось создать {_LINUX_ENVIRONMENT}: {exc}")

    for name, value in (
        ("http_proxy", proxy),
        ("https_proxy", proxy),
        ("HTTP_PROXY", proxy),
        ("HTTPS_PROXY", proxy),
        ("ALL_PROXY", proxy),
        ("ftp_proxy", proxy),
        ("NO_PROXY", noproxy),
        ("no_proxy", noproxy),
    ):
        os.environ[name] = value

    log(f"Системный proxy → {proxy} (profile.d + /etc/environment)")


def disable_linux_env_proxy(backup_path: Path, log: LogFn = _noop) -> None:
    """Restore corporate /etc/environment proxy and remove profile.d override."""
    if sys.platform == "win32":
        return

    try:
        _LINUX_PROFILE_D.unlink(missing_ok=True)
    except OSError as exc:
        log(f"Не удалось удалить {_LINUX_PROFILE_D}: {exc}")

    if not backup_path.is_file():
        log("Системный proxy: backup не найден — /etc/environment не трогали")
        return

    try:
        data = json.loads(backup_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Повреждён backup системного proxy: {exc}")
        return

    previous = data.get("proxy") if isinstance(data, dict) else None
    if not isinstance(previous, dict):
        previous = {}

    if _LINUX_ENVIRONMENT.is_file():
        try:
            current = _LINUX_ENVIRONMENT.read_text(encoding="utf-8")
            lines, _ = _parse_environment(current)
            kept: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.partition("=")[0].strip()
                    if key in _ENV_PROXY_KEYS:
                        continue
                kept.append(line)
            clean_prev: dict[str, str] = {}
            for key, raw in previous.items():
                val = str(raw).strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                clean_prev[str(key)] = val
            _LINUX_ENVIRONMENT.write_text(
                _render_environment(kept, clean_prev),
                encoding="utf-8",
            )
        except OSError as exc:
            log(f"Не удалось восстановить {_LINUX_ENVIRONMENT}: {exc}")
            return

    backup_path.unlink(missing_ok=True)

    # Drop bridge overrides from this process; leave corporate values if still in OS env
    for name in _ENV_PROXY_KEYS:
        cur = os.environ.get(name, "")
        if cur and re.search(r"127\.0\.0\.1:(1088|8877)", cur):
            os.environ.pop(name, None)

    log("Системный proxy восстановлен (/etc/environment, profile.d удалён)")


def enable_browser_pac(
    http_port: int,
    scope: str,
    bypass_count: int,
    backup_path: Path,
    log: LogFn = _noop,
    *,
    pac_url: str | None = None,
) -> None:
    if sys.platform == "win32":
        from desktop.win_proxy import enable_browser_pac as win_enable

        win_enable(
            http_port,
            scope,
            bypass_count,
            backup_path,
            log=log,
            pac_url=pac_url,
        )
        return

    pac_url = (pac_url or "").strip() or f"http://127.0.0.1:{http_port}/proxy.pac"
    # Backup previous GNOME mode if any
    if not backup_path.is_file():
        mode = "none"
        old_url = ""
        r = procutil.run(["gsettings", "get", "org.gnome.system.proxy", "mode"])
        if r.returncode == 0 and r.stdout:
            mode = r.stdout.strip().strip("'\"")
        r2 = procutil.run(
            ["gsettings", "get", "org.gnome.system.proxy", "autoconfig-url"]
        )
        if r2.returncode == 0 and r2.stdout:
            old_url = r2.stdout.strip().strip("'\"")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps({"mode": mode, "autoconfig_url": old_url}, indent=2),
            encoding="utf-8",
        )

    r = procutil.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "auto"])
    if r.returncode != 0:
        log("gsettings недоступен — только CLI: source ./var/cli.env")
        return
    procutil.run(
        [
            "gsettings",
            "set",
            "org.gnome.system.proxy",
            "autoconfig-url",
            pac_url,
        ]
    )
    if scope == "full":
        log(f"GNOME PAC FULL = {pac_url} (bypass={bypass_count})")
    else:
        log(f"GNOME PAC = {pac_url} (GitHub via VPS; bypass={bypass_count})")


def disable_browser_proxy(backup_path: Path, log: LogFn = _noop) -> None:
    if sys.platform == "win32":
        from desktop.win_proxy import disable_browser_proxy as win_disable

        win_disable(backup_path, log=log)
        return

    if backup_path.is_file():
        try:
            b = json.loads(backup_path.read_text(encoding="utf-8-sig"))
            mode = str(b.get("mode") or "none")
            url = str(b.get("autoconfig_url") or "")
            procutil.run(["gsettings", "set", "org.gnome.system.proxy", "mode", mode])
            if url:
                procutil.run(
                    [
                        "gsettings",
                        "set",
                        "org.gnome.system.proxy",
                        "autoconfig-url",
                        url,
                    ]
                )
            backup_path.unlink(missing_ok=True)
            log("GNOME proxy restored from backup")
            return
        except Exception:  # noqa: BLE001
            pass
    procutil.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"])
    backup_path.unlink(missing_ok=True)
    log("GNOME proxy disabled")
