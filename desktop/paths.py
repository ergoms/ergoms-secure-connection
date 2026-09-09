"""Resolve data root (next to exe / AppData) and bundled resource paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path


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


def _appdata_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "ops-content"


def data_root() -> Path:
    """Writable project root.

    - Dev: repository root
    - Frozen exe: %LOCALAPPDATA%\\ops-content (config imported via GUI)
    - Override: OPS_CONTENT_DATA
    """
    override = (os.environ.get("OPS_CONTENT_DATA") or "").strip()
    if override:
        return Path(override).expanduser()

    if is_frozen():
        return _appdata_root()

    return Path(__file__).resolve().parent.parent


def self_command() -> list[str]:
    """Argv prefix to re-invoke this app (PAC / watchdog / reverse-ssh)."""
    if is_frozen():
        return [str(Path(sys.executable).resolve())]
    return [sys.executable, "-m", "desktop"]


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
