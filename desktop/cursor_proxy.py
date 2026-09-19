"""Point Cursor at the local HTTP bridge (Linux has no GNOME proxy schemas)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from desktop.logutil import noop

LogFn = Callable[[str], None]

_PROXY_KEY = "http.proxy"
_PROXY_RE = re.compile(
    r'("http\.proxy"\s*:\s*)(?:"(?:\\.|[^"\\])*"|null)',
    re.I,
)


def desktop_home() -> Path:
    if sys.platform == "win32":
        return Path.home()
    user = (os.environ.get("SUDO_USER") or "").strip()
    if hasattr(os, "geteuid") and os.geteuid() == 0 and user and user != "root":
        try:
            import pwd

            return Path(pwd.getpwnam(user).pw_dir)
        except (ImportError, KeyError):
            pass
    return Path.home()


def cursor_settings_path() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Cursor" / "User" / "settings.json"
    return desktop_home() / ".config" / "Cursor" / "User" / "settings.json"


def _cursor_present(path: Path) -> bool:
    if path.is_file() or path.parent.is_dir():
        return True
    return path.parent.parent.is_dir()


def upsert_http_proxy(text: str, url: str) -> str:
    body = text if text.strip() else "{}\n"
    if _PROXY_RE.search(body):
        return _PROXY_RE.sub(rf'\1"{url}"', body, count=1)
    if not re.search(r"\{", body):
        return '{\n    "http.proxy": "%s"\n}\n' % url
    return re.sub(r"\{", '{\n    "http.proxy": "%s",' % url, body, count=1)


def _chown_to_session_user(path: Path) -> None:
    if sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    user = (os.environ.get("SUDO_USER") or "").strip()
    if not user or user == "root":
        return
    try:
        import pwd

        info = pwd.getpwnam(user)
        os.chown(path, info.pw_uid, info.pw_gid)
        if path.parent.is_dir():
            os.chown(path.parent, info.pw_uid, info.pw_gid)
    except (ImportError, KeyError, OSError):
        return


def set_cursor_http_proxy(
    proxy: str,
    log: LogFn = noop,
    *,
    backup_path: Path | None = None,
) -> None:
    if sys.platform == "win32":
        return
    path = cursor_settings_path()
    if not _cursor_present(path):
        return
    if backup_path is not None and not backup_path.is_file():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        backup_path.write_text(
            json.dumps({"path": str(path), "text": prev}, indent=2),
            encoding="utf-8",
        )
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else "{}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(upsert_http_proxy(text, proxy), encoding="utf-8")
    _chown_to_session_user(path)
    log(f"Cursor http.proxy = {proxy}")


def clear_cursor_http_proxy(
    log: LogFn = noop,
    *,
    backup_path: Path | None = None,
) -> None:
    if backup_path is None or not backup_path.is_file():
        return
    try:
        data = json.loads(backup_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        backup_path.unlink(missing_ok=True)
        return
    raw_path = str(data.get("path") or "") if isinstance(data, dict) else ""
    path = Path(raw_path) if raw_path else cursor_settings_path()
    prev = str(data.get("text") or "") if isinstance(data, dict) else ""
    try:
        if prev:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prev, encoding="utf-8")
            _chown_to_session_user(path)
        elif path.is_file() and path.read_text(encoding="utf-8-sig").strip() in (
            "{}",
            "{}\n",
        ):
            path.unlink()
    except OSError as exc:
        log(f"Cursor proxy restore: {exc}")
        return
    backup_path.unlink(missing_ok=True)
    log("Cursor http.proxy restored")
