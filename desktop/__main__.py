"""Unified entry: same commands on Linux/Windows, CLI or GUI."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


COMMANDS = (
    "init",
    "on",
    "off",
    "start",
    "stop",
    "status",
    "probe",
    "test",
    "sandbox",
    "tun-on",
    "tun-off",
    "reverse-on",
    "reverse-off",
    "download-sing-box",
    "sing-box",
    "docker-env",
    "docker-test",
    "watch",
    "install-service",
    "uninstall-service",
    "encrypt",
    "decrypt",
    "gui",
    "help",
)


def _has_display() -> bool:
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run_connect_socks(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ergoms-secure-connection connect-socks <host> <port>", file=sys.stderr)
        return 2
    import lib.connect_socks as connect_socks

    sys.argv = ["connect_socks.py", argv[0], argv[1]]
    return int(connect_socks.main())


def _show_help() -> int:
    print(
        """ERGOMS SECURE CONNECTION — клиент VLESS+Reality (Windows / Linux)

Конфиг: один файл config.json  (образец: config/config.example.json)

Команды (одинаковы везде):
  init                 создать config.json
  on / off             включить / выключить (TUN и kill switch по умолчанию вкл.)
  start / stop         то же, что on / off
  status               состояние
  probe HOST [PORT]    CONNECT через Squid
  test                 проверка обхода
  sandbox              песочница (мимо TUN/Amnezia; прямой Ethernet, свои :18080)
  tun-on / tun-off     TUN в процессе sing-box
  reverse-on / reverse-off  SSH с VPS на этот ПК (через SOCKS, не :22 офиса)
  download-sing-box    скачать sing-box в tools/
  docker-env           var/docker.env + compose (прокси для контейнеров)
  docker-test          проверка: curl из контейнера через мост
  watch                следить и переподключать (Ctrl+C)
  install-service      служба VPN + автозапуск (Linux: systemd; Windows: WinSW, админ)
  uninstall-service    убрать службу
  encrypt [OUT]        зашифровать config.json для передачи (пароль)
  decrypt [IN]         расшифровать в config.json
  gui                  окно Qt Quick (нужен дисплей, poetry install --extras gui)
  help

Транспорт: офис — VLESS+Reality через Squid; дом — Hysteria2 UDP :8443

Запуск:
  python -m desktop <cmd> …
  ./ergoms-secure-connection.sh <cmd> …           # Linux
  .\\ergoms-secure-connection.ps1 <cmd> …         # Windows
"""
    )
    return 0


def _parse_password_args(rest: list[str]) -> tuple[list[str], str | None]:
    password: str | None = None
    out: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-p", "--password") and i + 1 < len(rest):
            password = rest[i + 1]
            i += 2
            continue
        if arg.startswith("--password="):
            password = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, password


def _ask_password(*, confirm: bool = False) -> str:
    password = getpass.getpass("Password: ")
    if not password:
        raise SystemExit("Password is required")
    if confirm:
        again = getpass.getpass("Confirm password: ")
        if password != again:
            raise SystemExit("Passwords do not match")
    return password


def _run_service(cmd: str) -> int:
    import subprocess

    if sys.platform == "win32":
        script = _ROOT / "modes" / "windows" / f"{cmd}.ps1"
        if not script.is_file():
            print(f"[ERGOMS SECURE CONNECTION] ERROR: missing {script}", file=sys.stderr)
            return 1
        powershell = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            r"System32\WindowsPowerShell\v1.0\powershell.exe",
        )
        return int(
            subprocess.call(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ]
            )
        )
    script = _ROOT / "modes" / "linux" / f"{cmd}.sh"
    if not script.is_file():
        print(f"[ERGOMS SECURE CONNECTION] ERROR: missing {script}", file=sys.stderr)
        return 1
    return int(subprocess.call(["bash", str(script)]))


def _run_encrypt(rest: list[str]) -> int:
    from desktop.config_crypto import encrypt_file
    from desktop.config_io import apply_config, invoke_init
    from desktop.paths import Paths

    paths = Paths()
    paths.ensure_dirs()
    if not paths.config_path.is_file():
        invoke_init(paths)
    args, password = _parse_password_args(rest)
    out = Path(args[0]) if args else paths.root / "config.json.enc"
    if not out.is_absolute():
        out = Path.cwd() / out
    password = password or _ask_password(confirm=True)
    apply_config(paths.config_path, force=True, env_path=paths.env_path)
    encrypt_file(paths.config_path, out, password)
    print(f"[ERGOMS SECURE CONNECTION] encrypted → {out}")
    return 0


def _run_decrypt(rest: list[str]) -> int:
    from desktop.config_crypto import decrypt_file
    from desktop.config_io import apply_config, ensure_config_defaults, save_config
    from desktop.paths import Paths

    paths = Paths()
    paths.ensure_dirs()
    args, password = _parse_password_args(rest)
    src = Path(args[0]) if args else paths.root / "config.json.enc"
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.is_file():
        print(f"[ERGOMS SECURE CONNECTION] ERROR: file not found: {src}", file=sys.stderr)
        return 1
    password = password or _ask_password(confirm=False)
    cfg = decrypt_file(src, paths.config_path, password)
    save_config(paths.config_path, ensure_config_defaults(cfg))
    apply_config(paths.config_path, force=True)
    print(f"[ERGOMS SECURE CONNECTION] decrypted → {paths.config_path}")
    return 0


def _run_cli(cmd: str, rest: list[str]) -> int:
    if cmd == "encrypt":
        return _run_encrypt(rest)
    if cmd == "decrypt":
        return _run_decrypt(rest)
    if cmd in ("install-service", "uninstall-service"):
        return _run_service(cmd)

    from desktop.client import OpsClient

    def log(msg: str) -> None:
        print(f"[ERGOMS SECURE CONNECTION] {msg}", flush=True)

    client = OpsClient(log=log)
    try:
        if cmd == "init":
            client.init()
        elif cmd in ("on", "start"):
            if _elevate_cli_if_needed(client, "on", rest):
                return 0
            client.enable()
        elif cmd in ("off", "stop"):
            if _elevate_cli_if_needed(client, "off", rest):
                return 0
            client.disable()
        elif cmd == "status":
            st = client.status()
            for line in st["lines"]:
                print(line)
        elif cmd == "probe":
            host = rest[0] if rest else ""
            port = int(rest[1]) if len(rest) > 1 else 443
            if not host:
                print("usage: ergoms-secure-connection probe HOST [PORT]", file=sys.stderr)
                return 2
            return client.probe(host, port)
        elif cmd == "test":
            client.test_bypass()
        elif cmd == "sandbox":
            from desktop.sandbox import run_sandbox

            return run_sandbox(client, log=log)
        elif cmd == "tun-on":
            if _elevate_cli_if_needed(client, "tun-on", rest):
                return 0
            client.enable_tun()
        elif cmd == "tun-off":
            if _elevate_cli_if_needed(client, "tun-off", rest):
                return 0
            client.disable_tun()
        elif cmd == "reverse-on":
            client.enable_reverse_ssh()
        elif cmd == "reverse-off":
            client.disable_reverse_ssh()
        elif cmd in ("download-sing-box", "sing-box"):
            client.download_sing_box()
        elif cmd == "docker-env":
            client.docker_env()
        elif cmd == "docker-test":
            return client.docker_test()
        elif cmd == "watch":
            from desktop.watchdog import run_watch_forever

            from desktop.branding import ENV_WATCHDOG_CHILD, env

            daemon = "--daemon" in rest or env(ENV_WATCHDOG_CHILD).lower() in (
                "1",
                "true",
                "yes",
            )
            return run_watch_forever(client, log=log, daemon=daemon)
        elif cmd in ("help", "-h", "--help"):
            return _show_help()
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return _show_help() or 2
    except Exception as exc:  # noqa: BLE001
        print(f"[ERGOMS SECURE CONNECTION] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


_RESUME_FLAGS = {
    "--connect": "on",
    "--disconnect": "off",
    "--tun-on": "tun-on",
    "--tun-off": "tun-off",
}


def _elevate_cli_if_needed(client: object, action: str, rest: list[str]) -> bool:
    """Relaunch CLI elevated once. True = this process should exit."""
    from desktop import procutil
    from desktop.paths import self_command

    needs = getattr(client, "needs_elevation", None)
    if not callable(needs) or not needs(action=action):
        return False
    args = [*self_command(), action, *rest]
    ok = procutil.relaunch_as_admin(args, cwd=str(client.paths.root))  # type: ignore[attr-defined]
    if not ok:
        print(
            "[ERGOMS SECURE CONNECTION] нужны права администратора (TUN / kill switch)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return True


def _run_gui() -> int:
    if not _has_display():
        print(
            "[ERGOMS SECURE CONNECTION] GUI недоступен (нет DISPLAY). Используйте CLI, напр.: python -m desktop on",
            file=sys.stderr,
        )
        return _show_help() or 1
    from desktop.branding import ENV_RESUME, env
    from desktop.elevate import ensure_elevated_gui

    flags: list[str] = []
    from desktop import autostart

    if autostart.launched_from_autostart():
        flags.append("--autostart")
    resume = env(ENV_RESUME).lower()
    flags.extend(
        {
            "on": ["--connect"],
            "off": ["--disconnect"],
            "tun-on": ["--tun-on"],
            "tun-off": ["--tun-off"],
        }.get(resume, [])
    )
    from desktop.instance import activate_existing

    if activate_existing():
        return 0
    ensure_elevated_gui(flags)
    from desktop.gui import run_gui

    run_gui()
    return 0


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    autostart = False
    resume = ""
    kept: list[str] = []
    for arg in argv:
        if arg == "--autostart":
            autostart = True
            continue
        mapped = _RESUME_FLAGS.get(arg)
        if mapped:
            resume = mapped
            continue
        kept.append(arg)
    argv = kept
    if resume:
        from desktop.branding import ENV_RESUME

        os.environ[ENV_RESUME] = resume

    # No args: GUI when frozen (double-click exe); else help
    if not argv:
        from desktop.branding import ENV_GUI, env

        frozen = bool(getattr(sys, "frozen", False))
        if frozen or autostart or env(ENV_GUI).lower() in ("1", "true", "yes"):
            return _run_gui()
        return _show_help()

    head = argv[0].lower()
    if head in ("connect-socks", "socks-command"):
        return _run_connect_socks(argv[1:])
    if head == "pac-serve":
        from desktop.pac_serve import main as pac_main

        return int(pac_main(argv[1:]))
    if head in ("help", "-h", "--help"):
        return _show_help()
    if head == "gui":
        return _run_gui()
    if head in COMMANDS or head in ("download-sing-box", "sing-box"):
        return _run_cli(head, argv[1:])

    print(f"unknown command: {head}", file=sys.stderr)
    return _show_help() or 2


if __name__ == "__main__":
    raise SystemExit(main())
