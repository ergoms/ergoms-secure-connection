"""Register the GUI to start at user login (no admin)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from desktop.branding import APP_ID, APP_NAME, ENV_AUTOSTART, ENV_DATA, env
from desktop.paths import data_root, is_frozen

_RUN_NAME = APP_NAME
_LEGACY_RUN_NAMES = ("ERGOMS VPN", "ops-content")
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launched_from_autostart(argv: list[str] | None = None) -> bool:
    if env(ENV_AUTOSTART).lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    args = sys.argv[1:] if argv is None else argv
    return "--autostart" in args


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_command() is not None
    current = _linux_desktop().is_file()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    legacy = (config / "autostart" / "ops-content.desktop").is_file()
    return current or legacy


def enable() -> None:
    if sys.platform == "win32":
        _win_enable()
        return
    _linux_enable()


def disable() -> None:
    if sys.platform == "win32":
        _win_disable()
        return
    _linux_desktop().unlink(missing_ok=True)
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    (config / "autostart" / "ops-content.desktop").unlink(missing_ok=True)


def _gui_args() -> list[str]:
    if is_frozen():
        return [str(Path(sys.executable).resolve()), "--autostart"]
    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "python.exe":
        pw = exe.with_name("pythonw.exe")
        if pw.is_file():
            exe = pw
    return [str(exe), "-m", "desktop", "gui", "--autostart"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _quote_win(path: str) -> str:
    return f'"{path}"' if " " in path or path.endswith(".cmd") else path


def _win_command() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for name in (_RUN_NAME, *_LEGACY_RUN_NAMES):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
                value, _typ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        text = str(value or "").strip()
        if text:
            return text
    return None


def _win_enable() -> None:
    import winreg

    if is_frozen():
        cmd = f'"{Path(sys.executable).resolve()}" --autostart'
    else:
        cmd = f'"{_write_win_launcher()}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
        winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, cmd)


def _win_disable() -> None:
    try:
        import winreg
    except ImportError:
        return
    for name in (_RUN_NAME, *_LEGACY_RUN_NAMES):
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass


def _write_win_launcher() -> Path:
    root = _repo_root()
    data = data_root()
    path = data / "var" / "autostart.cmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    args = _gui_args()
    exe, rest = args[0], args[1:]
    extra = " ".join(_quote_win(a) for a in rest)
    path.write_text(
        "@echo off\r\n"
        f'set PYTHONPATH={root}\r\n'
        f'set {ENV_DATA}={data}\r\n'
        f'set {ENV_AUTOSTART}=1\r\n'
        f'start "" /D "{root}" {_quote_win(exe)} {extra}\r\n',
        encoding="utf-8",
    )
    return path


def _linux_desktop() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config / "autostart" / f"{APP_ID}.desktop"


def _linux_enable() -> None:
    dest = _linux_desktop()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = _gui_args()
    exec_line = " ".join(
        a if a.startswith("-") else f'"{a}"' if " " in a else a for a in args
    )
    cwd = _repo_root() if not is_frozen() else Path(sys.executable).resolve().parent
    dest.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={exec_line}\n"
        f"Path={cwd}\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
