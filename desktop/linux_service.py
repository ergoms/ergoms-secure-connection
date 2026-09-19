"""Install/remove the systemd unit for the frozen Linux client."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from desktop.branding import APP_ID, APP_NAME, CLI_NAME
from desktop.paths import linux_data_dir

UNIT_NAME = f"{APP_ID}.service"
UNIT_DST = Path("/etc/systemd/system") / UNIT_NAME
LEGACY_UNITS = ("ergoms-vpn.service", "ops-content.service")


def _real_user() -> tuple[str, str]:
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    if user == "root" and os.environ.get("SUDO_USER"):
        user = os.environ["SUDO_USER"]
    home = ""
    try:
        import pwd

        home = pwd.getpwnam(user).pw_dir
    except (ImportError, KeyError):
        home = os.environ.get("HOME") or "/root"
    return user, home


def systemd_quote(value: str) -> str:
    """Quote a word for a systemd unit (spaces cannot be backslash-escaped)."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', r"\"") + '"'


def systemd_exec(path: str | Path, *args: str) -> str:
    line = systemd_quote(str(path))
    if args:
        line += " " + " ".join(args)
    return line


def service_bin(exe: Path) -> Path:
    """Prefer /usr/local/bin/ergoms-sc — systemd rejects spaces in the executable name."""
    link = Path("/usr/local/bin") / CLI_NAME
    try:
        if link.exists() and link.resolve() == Path(exe).resolve():
            return link
    except OSError:
        pass
    return Path(exe)


def _reexec_root() -> None:
    exe = str(Path(sys.executable).resolve())
    os.execvp("sudo", ["sudo", "--preserve-env=PATH", exe, *sys.argv[1:]])


def render_unit(*, exe: Path, data: Path, home: str, user: str) -> str:
    bin_path = service_bin(exe)
    work = exe.parent
    start = systemd_exec(bin_path, "watch")
    stop = systemd_exec(bin_path, "off")
    return (
        "[Unit]\n"
        f"Description={APP_NAME} (VLESS+Reality)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        # WorkingDirectory= is a literal path. Quotes are not stripped (unlike
        # ExecStart=/Environment=) and systemd then reports "not absolute".
        f"WorkingDirectory={work}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        f"Environment={systemd_quote(f'ERGOMS_SC_DATA={data}')}\n"
        f"Environment={systemd_quote(f'HOME={home}')}\n"
        f"Environment={systemd_quote(f'USER={user}')}\n"
        "Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        f"ExecStart={start}\n"
        f"ExecStop={stop}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "KillMode=mixed\n"
        "TimeoutStartSec=90\n"
        "TimeoutStopSec=20\n"
        "Nice=-5\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _write_unit(*, exe: Path, data: Path, home: str, user: str) -> None:
    UNIT_DST.write_text(
        render_unit(exe=exe, data=data, home=home, user=user),
        encoding="utf-8",
    )
    UNIT_DST.chmod(0o644)


def _stop_client(exe: Path, home: str, user: str, data: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = home
    env["USER"] = user
    env["ERGOMS_SC_DATA"] = str(data)
    subprocess.call([str(exe), "off"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install() -> int:
    if not hasattr(os, "geteuid"):
        print("systemd service is Linux-only", file=sys.stderr)
        return 1
    if os.geteuid() != 0:
        if not shutil_which("sudo"):
            print(f"Нужен root: sudo {CLI_NAME} install-service", file=sys.stderr)
            return 1
        _reexec_root()
        return 1
    if not shutil_which("systemctl"):
        print("systemctl not found — нужен systemd", file=sys.stderr)
        return 1

    exe = Path(sys.executable).resolve()
    if not exe.is_file():
        print(f"нет бинарника: {exe}", file=sys.stderr)
        return 1
    user, home = _real_user()
    data = linux_data_dir(Path(home))
    data.mkdir(parents=True, exist_ok=True)
    if not (data / "config.json").is_file():
        print(
            f"Нет {data / 'config.json'} — сначала импортируйте конфиг "
            f"(окно {CLI_NAME} → Настройки → Из файла) или: {CLI_NAME} init",
            file=sys.stderr,
        )
        return 1

    _stop_client(exe, home, user, data)
    for legacy in LEGACY_UNITS:
        subprocess.call(["systemctl", "disable", "--now", legacy], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Path("/etc/systemd/system", legacy).unlink(missing_ok=True)

    _write_unit(exe=exe, data=data, home=home, user=user)
    subprocess.check_call(["systemctl", "daemon-reload"])
    try:
        subprocess.check_call(["systemctl", "enable", "--now", UNIT_NAME])
    except subprocess.CalledProcessError:
        print(f"не удалось запустить {UNIT_NAME} — смотрите systemctl status", file=sys.stderr)
        subprocess.call(["systemctl", "--no-pager", "--full", "status", UNIT_NAME])
        return 1
    print(f"Installed: {UNIT_DST}")
    print("Start:     systemctl enable --now ergoms-secure-connection")
    print("Status:    systemctl status ergoms-secure-connection")
    print("Logs:      journalctl -u ergoms-secure-connection -f")
    print(f"Remove:    {CLI_NAME} uninstall-service")
    subprocess.call(["systemctl", "--no-pager", "--full", "status", UNIT_NAME])
    return 0


def uninstall() -> int:
    if not hasattr(os, "geteuid"):
        return 1
    if os.geteuid() != 0:
        if shutil_which("sudo"):
            _reexec_root()
        print(f"Нужен root: sudo {CLI_NAME} uninstall-service", file=sys.stderr)
        return 1
    if shutil_which("systemctl"):
        for name in (UNIT_NAME, *LEGACY_UNITS):
            subprocess.call(["systemctl", "disable", "--now", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.call(["systemctl", "reset-failed", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            Path("/etc/systemd/system", name).unlink(missing_ok=True)
            print(f"Removed {name}")
        subprocess.call(["systemctl", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)
