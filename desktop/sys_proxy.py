"""OS system proxy (PAC): Windows Internet Settings or GNOME gsettings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from desktop import procutil

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def enable_browser_pac(
    http_port: int,
    scope: str,
    bypass_count: int,
    backup_path: Path,
    log: LogFn = _noop,
) -> None:
    if sys.platform == "win32":
        from desktop.win_proxy import enable_browser_pac as win_enable

        win_enable(http_port, scope, bypass_count, backup_path, log=log)
        return

    pac_url = f"http://127.0.0.1:{http_port}/proxy.pac"
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
