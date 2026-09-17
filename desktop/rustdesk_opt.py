"""Keep RustDesk off the TUN LAN so it uses the VPS relay, not a fake punch."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

from desktop.logutil import noop
from desktop.tun import RUSTDESK_PROCS, TUN_LAN_CIDR

LogFn = Callable[[str], None]

FW_RULE = "ERGOMS rustdesk no-tun-lan"
_ALWAYS_RELAY = {
    "allow-always-relay": "Y",
    "force-always-relay": "Y",
}


def rustdesk_config_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "RustDesk" / "config" / "RustDesk2.toml"


def rustdesk_service_config_path() -> Path:
    return Path(
        r"C:\Windows\ServiceProfiles\LocalService\AppData\Roaming"
        r"\RustDesk\config\RustDesk2.toml"
    )


def rustdesk_config_paths() -> list[Path]:
    """User UI and the Windows service use different RustDesk2.toml files."""
    found: list[Path] = []
    seen: set[str] = set()
    for path in (rustdesk_config_path(), rustdesk_service_config_path()):
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            found.append(path)
    return found


def rustdesk_peers_dir() -> Path:
    return rustdesk_config_path().parent / "peers"


def rustdesk_exe_paths() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [
        Path(pf) / "RustDesk" / "rustdesk.exe",
        Path(pf) / "RustDesk" / "rustdesk_service.exe",
        Path(local) / "Programs" / "RustDesk" / "rustdesk.exe" if local else None,
    ]
    for raw in candidates:
        if raw is None:
            continue
        try:
            path = raw.resolve() if raw.is_file() else raw
        except OSError:
            path = raw
        if not path.is_file():
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    return found


def rustdesk_tun_lan_reject_rules() -> list[dict[str, object]]:
    """Punch to 172.19.* is the TUN address, not the peer. Fail it → real :21117."""
    return [
        {
            "process_name": list(RUSTDESK_PROCS),
            "ip_cidr": [TUN_LAN_CIDR],
            "action": "reject",
        }
    ]


def _options_block_bounds(text: str) -> tuple[int, int]:
    start = text.find("[options]")
    if start < 0:
        return -1, -1
    body = start + len("[options]")
    nxt = text.find("\n[", body)
    return body, len(text) if nxt < 0 else nxt


def _option_value(block: str, key: str) -> str | None:
    needle = f"{key} ="
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(needle) or stripped.startswith(f"{key}="):
            _, _, rest = stripped.partition("=")
            return rest.strip().strip("'\"")
    return None


def set_toml_options(path: Path, updates: dict[str, str]) -> dict[str, str | None]:
    """Write keys into [options]. Returns previous values (None if absent)."""
    text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    prev: dict[str, str | None] = {}
    if "[options]" not in text:
        text = (text.rstrip() + "\n\n[options]\n") if text.strip() else "[options]\n"
    body, end = _options_block_bounds(text)
    block = text[body:end]
    for key, value in updates.items():
        prev[key] = _option_value(block, key)
        line = f"{key} = '{value}'"
        replaced = False
        lines: list[str] = []
        for raw in block.splitlines(keepends=True):
            stripped = raw.strip()
            if stripped.startswith(f"{key} =") or stripped.startswith(f"{key}="):
                nl = "\n" if raw.endswith("\n") else ""
                lines.append(line + nl)
                replaced = True
            else:
                lines.append(raw)
        if not replaced:
            prefix = "" if (not block or block.endswith("\n")) else "\n"
            lines.append(f"{prefix}{line}\n")
        block = "".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text[:body] + block + text[end:], encoding="utf-8")
    return prev


def restore_toml_options(path: Path, previous: dict[str, str | None]) -> None:
    if not path.is_file() or not previous:
        return
    text = path.read_text(encoding="utf-8-sig")
    body, end = _options_block_bounds(text)
    if body < 0:
        return
    block = text[body:end]
    lines: list[str] = []
    skip = {k for k, v in previous.items() if v is None}
    replace = {k: v for k, v in previous.items() if v is not None}
    for raw in block.splitlines(keepends=True):
        stripped = raw.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key in skip:
            continue
        if key in replace:
            nl = "\n" if raw.endswith("\n") else ""
            lines.append(f"{key} = '{replace[key]}'{nl}")
            continue
        lines.append(raw)
    path.write_text(text[:body] + "".join(lines) + text[end:], encoding="utf-8")


def enable_rustdesk_always_relay(backup_path: Path, log: LogFn = noop) -> None:
    paths = rustdesk_config_paths()
    if not paths:
        return
    first_prev: dict[str, str | None] | None = None
    for cfg in paths:
        prev = set_toml_options(cfg, dict(_ALWAYS_RELAY))
        if first_prev is None:
            first_prev = prev
        # Binding to Ethernet (local-ip-addr) sends :21116 past TUN; kill
        # switch then drops it. Incoming sessions use the service toml.
        restore_toml_options(cfg, {"local-ip-addr": None})
        peers = cfg.parent / "peers"
        if peers.is_dir():
            for peer in peers.glob("*.toml"):
                set_toml_options(peer, {"force-always-relay": "Y"})
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if first_prev is not None and not backup_path.is_file():
        backup_path.write_text(
            json.dumps(first_prev, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log(
        "RustDesk: always-relay — punch в 172.19.* режет firewall, "
        f"конфиг ({len(paths)}): идём на ретранслятор через TUN"
    )


def restore_rustdesk_always_relay(backup_path: Path, log: LogFn = noop) -> None:
    prev: dict[str, str | None] = {}
    if backup_path.is_file():
        try:
            loaded = json.loads(backup_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            prev = {
                str(k): (None if v is None else str(v))
                for k, v in loaded.items()
                if k in _ALWAYS_RELAY
            }
        backup_path.unlink(missing_ok=True)
    for cfg in rustdesk_config_paths():
        if prev:
            restore_toml_options(cfg, prev)
        restore_toml_options(cfg, {"local-ip-addr": None})
    log("RustDesk: always-relay вернул")


def enable_rustdesk_tun_lan_firewall(log: LogFn = noop) -> None:
    if sys.platform != "win32":
        return
    from desktop import procutil

    exes = rustdesk_exe_paths()
    if not exes:
        return
    procutil.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"],
        timeout=8.0,
    )
    ok = 0
    for exe in exes:
        r = procutil.run(
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={FW_RULE}",
                "dir=out",
                "action=block",
                f"program={exe}",
                f"remoteip={TUN_LAN_CIDR}",
                "enable=yes",
            ],
            timeout=8.0,
        )
        if r.returncode == 0:
            ok += 1
    if ok:
        log(f"RustDesk: firewall режет {TUN_LAN_CIDR} ({ok} exe) — иначе punch в свой TUN")
    else:
        log("RustDesk: firewall правило не встало (нужны права администратора)")


def disable_rustdesk_tun_lan_firewall(log: LogFn = noop) -> None:
    if sys.platform != "win32":
        return
    from desktop import procutil

    r = procutil.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"],
        timeout=8.0,
    )
    if r.returncode == 0:
        log("RustDesk: firewall 172.19.* снят")


def enable_rustdesk_vpn_guards(backup_path: Path, log: LogFn = noop) -> None:
    enable_rustdesk_always_relay(backup_path, log=log)
    enable_rustdesk_tun_lan_firewall(log=log)


def disable_rustdesk_vpn_guards(backup_path: Path, log: LogFn = noop) -> None:
    disable_rustdesk_tun_lan_firewall(log=log)
    restore_rustdesk_always_relay(backup_path, log=log)
