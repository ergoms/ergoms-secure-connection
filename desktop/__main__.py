"""Unified entry: same commands on Linux/Windows, CLI or GUI."""

from __future__ import annotations

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

Конфиг: .env + config.json  (образцы в config/)

Команды (одинаковы везде):
  init                 создать .env / config.json
  on / off             включить / выключить (+ TUN если TUN=1)
  start / stop         то же, что on / off
  status               состояние
  probe HOST [PORT]    CONNECT через Squid
  test                 проверка обхода
  tun-on / tun-off     TUN в процессе sing-box
  download-sing-box    скачать sing-box в tools/
  docker-env           var/docker.env + compose (прокси для контейнеров)
  docker-test          проверка: curl из контейнера через мост
  watch                следить и переподключать (Ctrl+C)
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


def _run_cli(cmd: str, rest: list[str]) -> int:
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
