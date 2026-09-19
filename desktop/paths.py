"""Resolve data root (next to exe / AppData) and bundled resource paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from desktop.branding import APP_ID, APP_NAME, ENV_DATA, env


def _proxy_backup_name() -> str:
    return "winproxy.bak.json" if sys.platform == "win32" else "linuxproxy.bak.json"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def bundle_dir() -> Path:
    """Read-only resources shipped inside the frozen app (or repo root in dev)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _under_program_files(path: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        resolved = str(path.resolve()).lower()
    except OSError:
        resolved = str(path).lower()
    candidates = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"),
    ]
    for base in candidates:
        if not base:
            continue
        try:
            prefix = str(Path(base).resolve()).lower()
        except OSError:
            prefix = base.lower()
        if resolved == prefix or resolved.startswith(prefix + os.sep):
            return True
    return False


def _invoking_linux_home() -> Path | None:
    """Home of the user who ran sudo, so `sudo ergoms-sc` does not use /root."""
    user = (os.environ.get("SUDO_USER") or "").strip()
    if not user or user == "root":
        return None
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        return None
    try:
        import pwd

        return Path(pwd.getpwnam(user).pw_dir)
    except (ImportError, KeyError, OSError):
        return None


def linux_data_dir(home: Path | None = None) -> Path:
    """XDG data dir for the frozen Linux client (does not create it)."""
    if home is None:
        sudo_home = _invoking_linux_home()
        xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
        # sudo typically points XDG at /root — keep the real user's tree.
        if xdg and not sudo_home:
            return Path(xdg).expanduser() / APP_ID
        home = sudo_home or Path.home()
    return Path(home) / ".local" / "share" / APP_ID


def _copy_if_missing(src: Path, dst: Path) -> None:
    if dst.is_file() or not src.is_file():
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    except OSError:
        pass


def _migrate_legacy_config(current: Path, *legacy_roots: Path) -> None:
    dst = current / "config.json"
    for base in legacy_roots:
        for name in (APP_NAME, "ERGOMS VPN", "ops-content"):
            src_dir = base / name
            if not (src_dir / "config.json").is_file():
                continue
            _copy_if_missing(src_dir / "config.json", dst)
            _copy_if_missing(src_dir / "amneziawg.conf", current / "amneziawg.conf")
            return


def _appdata_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    base = Path(local)
    current = base / APP_NAME
    current.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_config(current, base)
    return current


def _linux_frozen_root() -> Path:
    current = linux_data_dir()
    current.mkdir(parents=True, exist_ok=True)
    legacy = Path.home() / "AppData" / "Local"
    extra: list[Path] = [legacy]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        extra.insert(0, Path(local))
    _migrate_legacy_config(current, *extra)
    return current


def data_root() -> Path:
    """Writable project root.

    - Dev: repository root
    - Frozen Windows: %LOCALAPPDATA%\\ERGOMS SECURE CONNECTION
    - Frozen Linux: $XDG_DATA_HOME/ergoms-secure-connection
      or ~/.local/share/ergoms-secure-connection
    - Override: ERGOMS_SC_DATA
    """
    override = env(ENV_DATA)
    if override:
        return Path(override).expanduser()

    if is_frozen():
        if sys.platform == "win32":
            return _appdata_root()
        return _linux_frozen_root()

    return Path(__file__).resolve().parent.parent


def self_command() -> list[str]:
    """Argv prefix to re-invoke this app (PAC / watchdog / reverse-ssh)."""
    if is_frozen():
        return [str(Path(sys.executable).resolve())]
    return [sys.executable, "-m", "desktop"]


def gui_command(*flags: str) -> list[str]:
    """Argv to open the GUI (frozen exe or python -m desktop gui)."""
    if is_frozen():
        return [str(Path(sys.executable).resolve()), *flags]
    return [sys.executable, "-m", "desktop", "gui", *flags]


def resolve_ssh_identity(creds_dir: Path | None = None) -> str:
    """Auto-pick a private key from creds/ (no config setting)."""
    root = creds_dir or (data_root() / "creds")
    if not root.is_dir():
        return ""
    preferred = ("server-vps", "id_ed25519", "id_rsa", "id_ecdsa", "id_dsa")
    for name in preferred:
        path = root / name
        if path.is_file():
            return str(path.resolve())
    skip = {
        ".gitkeep",
        "ssh_known_hosts",
        "ssh_known_hosts.old",
    }
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name in skip or name.endswith(".pub") or name.endswith(".old"):
            continue
        return str(path.resolve())
    return ""


class Paths:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_root()
        self.config_dir = self.root / "config"
        self.creds_dir = self.root / "creds"
        self.logs_dir = self.root / "logs"
        self.var_dir = self.root / "var"
        self.tools_dir = self.root / "tools"
        self.config_path = self.root / "config.json"
        self.awg_conf_path = self.root / "amneziawg.conf"
        self.env_path = self.root / ".env"
        self.known_hosts = self.creds_dir / "ssh_known_hosts"
        self.bridge_pid = self.var_dir / "bridge.pid"
        self.pac_pid = self.var_dir / "pac.pid"
        self.state_path = self.var_dir / "state.json"
        self.proxy_backup = self.var_dir / _proxy_backup_name()
        # Backup of /etc/environment proxy keys while SOCKS bridge is active
        self.env_proxy_backup = self.var_dir / "linuxenv.proxy.bak.json"
        self.cli_env = self.var_dir / "cli.env"
        self.cli_ps1 = self.var_dir / "cli.ps1"
        self.docker_env = self.var_dir / "docker.env"
        self.docker_compose_proxy = self.var_dir / "docker-compose.proxy.yml"
        self.docker_hosts = self.var_dir / "docker.hosts"
        self.docker_run_ps1 = self.var_dir / "docker-run.ps1"
        self.docker_run_sh = self.var_dir / "docker-run.sh"
        self.docker_proxy_backup = self.var_dir / "docker-proxy.bak.json"
        self.git_proxy_backup = self.var_dir / "gitproxy.bak.json"
        self.cursor_proxy_backup = self.var_dir / "cursorproxy.bak.json"
        self.rustdesk_opt_backup = self.var_dir / "rustdesk-opt.bak.json"
        self.watchdog_pid = self.var_dir / "watchdog.pid"

    def ensure_dirs(self) -> None:
        (self.creds_dir / "certs").mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        (self.root / "lib").mkdir(parents=True, exist_ok=True)
        if not self.known_hosts.exists():
            self.known_hosts.touch()
