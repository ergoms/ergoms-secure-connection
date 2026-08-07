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
    "tun-on",
    "tun-off",
    "download-sing-box",
    "sing-box",
    "docker-env",
    "docker-test",
    "watch",
    "encrypt",
    "decrypt",
    "gui",
    "help",
)


def _has_display() -> bool:
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run_connect(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ops-content connect <host> <port>", file=sys.stderr)
        return 2
    import lib.connect_proxy as connect_proxy

    sys.argv = ["connect_proxy.py", argv[0], argv[1]]
    return int(connect_proxy.main())


def _run_bridge(argv: list[str]) -> int:
    import lib.http_via_socks as http_via_socks

    sys.argv = ["http_via_socks.py", *argv]
    return int(http_via_socks.main())


def _show_help() -> int:
    print(
        """ops-content — клиент VLESS+Reality (Windows / Linux)

Конфиг: один файл config.json  (образец: config/config.example.json)

Команды (одинаковы везде):
  init                 создать config.json
  on / off             включить / выключить (+ TUN если tun.enabled)
  start / stop         то же, что on / off
  status               состояние
  probe HOST [PORT]    CONNECT через Squid
  test                 проверка обхода
  tun-on / tun-off     TUN в процессе sing-box
  download-sing-box    скачать sing-box в tools/
  docker-env           var/docker.env + compose (прокси для контейнеров)
  docker-test          проверка: curl из контейнера через мост
  watch                следить и переподключать (Ctrl+C)
  encrypt [OUT]        зашифровать config.json для передачи (пароль)
  decrypt [IN]         расшифровать в config.json
  gui                  окно (нужен дисплей)
  help

Транспорт: VLESS+Reality через корпоративный Squid на VPS :443

Запуск:
  python -m desktop <cmd> …
  ./ops-content.sh <cmd> …          # Linux
  .\\ops-content.ps1 <cmd> …        # Windows
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
    print(f"[ops-content] encrypted → {out}")
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
        print(f"[ops-content] ERROR: file not found: {src}", file=sys.stderr)
        return 1
    password = password or _ask_password(confirm=False)
    cfg = decrypt_file(src, paths.config_path, password)
    save_config(paths.config_path, ensure_config_defaults(cfg))
    apply_config(paths.config_path, force=True)
    print(f"[ops-content] decrypted → {paths.config_path}")
    return 0


def _run_cli(cmd: str, rest: list[str]) -> int:
    if cmd == "encrypt":
        return _run_encrypt(rest)
    if cmd == "decrypt":
        return _run_decrypt(rest)

    from desktop.client import OpsClient

    def log(msg: str) -> None:
        print(f"[ops-content] {msg}", flush=True)

    client = OpsClient(log=log)
    try:
        if cmd == "init":
            client.init()
        elif cmd in ("on", "start"):
            client.enable()
        elif cmd in ("off", "stop"):
            client.disable()
        elif cmd == "status":
            st = client.status()
            for line in st["lines"]:
                print(line)
        elif cmd == "probe":
            host = rest[0] if rest else ""
            port = int(rest[1]) if len(rest) > 1 else 443
            if not host:
                print("usage: ops-content probe HOST [PORT]", file=sys.stderr)
                return 2
            return client.probe(host, port)
        elif cmd == "test":
            client.test_bypass()
        elif cmd == "tun-on":
            client.enable_tun()
        elif cmd == "tun-off":
            client.disable_tun()
        elif cmd in ("download-sing-box", "sing-box"):
            client.download_sing_box()
        elif cmd == "docker-env":
            client.docker_env()
        elif cmd == "docker-test":
            return client.docker_test()
        elif cmd == "watch":
            from desktop.watchdog import run_watch_forever

            daemon = "--daemon" in rest or os.environ.get(
                "OPS_CONTENT_WATCHDOG_CHILD", ""
            ).strip() in ("1", "true", "yes")
            return run_watch_forever(client, log=log, daemon=daemon)
        elif cmd in ("help", "-h", "--help"):
            return _show_help()
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            return _show_help() or 2
    except Exception as exc:  # noqa: BLE001
        print(f"[ops-content] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_gui() -> int:
    if not _has_display():
        print(
            "[ops-content] GUI недоступен (нет DISPLAY). Используйте CLI, напр.: python -m desktop on",
            file=sys.stderr,
        )
        return _show_help() or 1
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

    # No args: help (explicit: gui)
    if not argv:
        if os.environ.get("OPS_CONTENT_GUI", "").strip() in ("1", "true", "yes"):
            return _run_gui()
        return _show_help()

    head = argv[0].lower()
    if head in ("connect", "proxy-command"):
        return _run_connect(argv[1:])
    if head == "bridge":
        return _run_bridge(argv[1:])
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
