"""Manage AmneziaWG client keys in creds/awg/ and rebuild the server peer list."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from desktop.config_io import default_config_template, render_amnezia_conf  # noqa: E402

STATE = Path("/var/lib/ops-content-singbox")
CREDS = STATE / "credentials.env"
SERVER_CONF = Path("/etc/amnezia/amneziawg/awg0.conf")
CLIENTS_DIR = _ROOT / "creds" / "awg"
PEERS_PATH = CLIENTS_DIR / "peers.json"
SUBNET = "10.66.66"
SERVER_HOST_ID = 1
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _chmod600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _public_ip() -> str:
    try:
        got = subprocess.check_output(
            ["curl", "-4", "-fsS", "--max-time", "8", "ifconfig.me"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if got:
            return got
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        got = subprocess.check_output(["hostname", "-I"], text=True).split()
        if got:
            return got[0]
    except (OSError, subprocess.CalledProcessError):
        pass
    return os.environ.get("PUBLIC_IP") or "YOUR_VPS_IP"


def _wan_iface() -> str:
    try:
        line = subprocess.check_output(
            ["ip", "-4", "route", "get", "1.1.1.1"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "eth0"
    parts = line.split()
    for i, tok in enumerate(parts):
        if tok == "dev" and i + 1 < len(parts):
            return parts[i + 1]
    return "eth0"


def _awg_bin(cmd: str) -> str:
    try:
        return subprocess.check_output(["awg", cmd], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("нужен awg (сначала bash modes/vps/enable_amneziawg.sh)") from exc


def _awg_pubkey(private: str) -> str:
    return subprocess.check_output(
        ["awg", "pubkey"],
        input=private + "\n",
        text=True,
    ).strip()


def sanitize_name(raw: str) -> str:
    name = str(raw or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(f"плохое имя клиента: {raw!r} (буквы, цифры, . _ -)")
    return name


def used_host_ids(peers: list[dict[str, Any]]) -> set[int]:
    used = {SERVER_HOST_ID}
    for peer in peers:
        addr = str(peer.get("address") or "").split("/")[0].strip()
        parts = addr.split(".")
        if len(parts) == 4:
            try:
                used.add(int(parts[3]))
            except ValueError:
                continue
    return used


def next_address(peers: list[dict[str, Any]]) -> str:
    used = used_host_ids(peers)
    for host_id in range(2, 255):
        if host_id not in used:
            return f"{SUBNET}.{host_id}/32"
    raise RuntimeError(f"нет свободных адресов в {SUBNET}.0/24")


def next_name(existing: set[str]) -> str:
    if "pc" not in existing:
        return "pc"
    n = 2
    while f"pc{n}" in existing:
        n += 1
    return f"pc{n}"


def load_peers(path: Path = PEERS_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        clients = data
    elif isinstance(data, dict) and isinstance(data.get("clients"), list):
        clients = data["clients"]
    else:
        return []
    return [p for p in clients if isinstance(p, dict) and p.get("name")]


def save_peers(peers: list[dict[str, Any]], path: Path = PEERS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"clients": peers}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _chmod600(path)


def _new_keys() -> tuple[str, str, str]:
    private = _awg_bin("genkey")
    public = _awg_pubkey(private)
    psk = _awg_bin("genpsk")
    return private, public, psk


def add_clients(
    names: list[str],
    *,
    count: int = 0,
    peers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    current = list(peers if peers is not None else load_peers())
    taken = {str(p.get("name") or "") for p in current}
    wanted: list[str] = []
    for raw in names:
        name = sanitize_name(raw)
        if name in taken or name in wanted:
            raise ValueError(f"клиент {name} уже есть")
        wanted.append(name)
    extra = max(0, int(count or 0) - len(wanted))
    for _ in range(extra):
        name = next_name(taken | set(wanted))
        wanted.append(name)
    if not wanted:
        wanted.append(next_name(taken))
    created: list[dict[str, Any]] = []
    for name in wanted:
        private, public, psk = _new_keys()
        peer = {
            "name": name,
            "private_key": private,
            "public_key": public,
            "psk": psk,
            "address": next_address(current + created),
        }
        created.append(peer)
        current.append(peer)
    save_peers(current)
    return created


def migrate_legacy(creds: dict[str, str], peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if peers:
        return peers
    priv = creds.get("AWG_CLIENT_PRIVATE") or ""
    pub = creds.get("AWG_CLIENT_PUBLIC") or ""
    if not priv:
        return peers
    if not pub:
        try:
            pub = _awg_pubkey(priv)
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            pub = ""
    peers.append(
        {
            "name": "pc",
            "private_key": priv,
            "public_key": pub,
            "psk": creds.get("AWG_PSK") or "",
            "address": creds.get("AWG_ADDRESS_CLIENT") or f"{SUBNET}.2/32",
        }
    )
    save_peers(peers)
    return peers


def ensure_first_client(creds: dict[str, str] | None = None) -> list[dict[str, Any]]:
    env = creds if creds is not None else _env_file(CREDS)
    peers = migrate_legacy(env, load_peers())
    if peers:
        return peers
    if not (env.get("AWG_SERVER_PRIVATE") or "").strip():
        return []
    add_clients([], count=1, peers=[])
    return load_peers()


def render_server_conf(
    creds: dict[str, str],
    peers: list[dict[str, Any]],
    *,
    wan_iface: str,
) -> str:
    port = creds.get("AWG_PORT") or "51820"
    server_priv = creds.get("AWG_SERVER_PRIVATE") or ""
    server_addr = creds.get("AWG_ADDRESS_SERVER") or f"{SUBNET}.1/24"
    lines = [
        "[Interface]",
        f"Address = {server_addr}",
        f"ListenPort = {port}",
        f"PrivateKey = {server_priv}",
        f"Jc = {creds.get('AWG_JC') or '0'}",
        f"Jmin = {creds.get('AWG_JMIN') or '0'}",
        f"Jmax = {creds.get('AWG_JMAX') or '0'}",
        f"S1 = {creds.get('AWG_S1') or '0'}",
        f"S2 = {creds.get('AWG_S2') or '0'}",
        f"H1 = {creds.get('AWG_H1') or ''}",
        f"H2 = {creds.get('AWG_H2') or ''}",
        f"H3 = {creds.get('AWG_H3') or ''}",
        f"H4 = {creds.get('AWG_H4') or ''}",
        (
            "PostUp = iptables -A FORWARD -i awg0 -j ACCEPT; "
            f"iptables -A FORWARD -o awg0 -j ACCEPT; "
            f"iptables -t nat -A POSTROUTING -o {wan_iface} -j MASQUERADE"
        ),
        (
            "PostDown = iptables -D FORWARD -i awg0 -j ACCEPT; "
            f"iptables -D FORWARD -o awg0 -j ACCEPT; "
            f"iptables -t nat -D POSTROUTING -o {wan_iface} -j MASQUERADE"
        ),
    ]
    for peer in peers:
        lines.extend(
            [
                "",
                "[Peer]",
                f"PublicKey = {peer.get('public_key') or ''}",
                f"PresharedKey = {peer.get('psk') or ''}",
                f"AllowedIPs = {peer.get('address') or ''}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_client_json(creds: dict[str, str], host: str, *, has_awg: bool) -> Path:
    cfg = deepcopy(default_config_template())
    cfg["server"]["host"] = host
    cfg["transport"]["dial"] = "amneziawg" if has_awg else "vless-reality"
    cfg["transport"]["uuid"] = creds.get("UUID") or ""
    cfg["transport"]["public_key"] = creds.get("PUBLIC_KEY") or ""
    cfg["transport"]["short_id"] = creds.get("SHORT_ID") or ""
    cfg["transport"]["server_name"] = creds.get("SERVER_NAME") or "www.cloudflare.com"
    tr = cfg.get("transport")
    if isinstance(tr, dict):
        tr.pop("amneziawg", None)
        tr.pop("hysteria2", None)
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    dests = [CLIENTS_DIR / "client.json"]
    STATE.mkdir(parents=True, exist_ok=True)
    dests.append(STATE / "client.json")
    for path in dests:
        path.write_text(text, encoding="utf-8")
        _chmod600(path)
    return dests[0]


def write_client_confs(creds: dict[str, str], peers: list[dict[str, Any]], host: str) -> list[Path]:
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    port = int(creds.get("AWG_PORT") or "51820")
    server_pub = creds.get("AWG_SERVER_PUBLIC") or ""
    written: list[Path] = []
    for peer in peers:
        text = render_amnezia_conf(
            {
                "private_key": peer.get("private_key") or "",
                "peer_public_key": server_pub,
                "pre_shared_key": peer.get("psk") or "",
                "address": peer.get("address") or f"{SUBNET}.2/32",
                "port": port,
                "jc": int(creds.get("AWG_JC") or "0"),
                "jmin": int(creds.get("AWG_JMIN") or "0"),
                "jmax": int(creds.get("AWG_JMAX") or "0"),
                "s1": int(creds.get("AWG_S1") or "0"),
                "s2": int(creds.get("AWG_S2") or "0"),
                "h1": creds.get("AWG_H1") or "",
                "h2": creds.get("AWG_H2") or "",
                "h3": creds.get("AWG_H3") or "",
                "h4": creds.get("AWG_H4") or "",
            },
            host=host,
        )
        path = CLIENTS_DIR / f"{peer['name']}.conf"
        path.write_text(text, encoding="utf-8")
        _chmod600(path)
        written.append(path)
    if written:
        STATE.mkdir(parents=True, exist_ok=True)
        fallback = STATE / "amneziawg.conf"
        fallback.write_text(written[0].read_text(encoding="utf-8"), encoding="utf-8")
        _chmod600(fallback)
    return written


def emit(*, apply: bool = False) -> int:
    creds = _env_file(CREDS)
    peers = ensure_first_client(creds)
    host = _public_ip()
    json_path = write_client_json(creds, host, has_awg=bool(peers))
    confs = write_client_confs(creds, peers, host)
    if apply and creds.get("AWG_SERVER_PRIVATE"):
        SERVER_CONF.parent.mkdir(parents=True, exist_ok=True)
        SERVER_CONF.write_text(
            render_server_conf(creds, peers, wan_iface=_wan_iface()),
            encoding="utf-8",
        )
        _chmod600(SERVER_CONF)
        try:
            subprocess.run(
                ["systemctl", "restart", "ergoms-amneziawg.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
    print(f"JSON: {json_path}")
    print(f"Ключи: {CLIENTS_DIR} ({len(peers)} шт.)")
    for peer in peers:
        print(f"  {peer.get('name')}.conf  {peer.get('address')}")
    print("В клиенте: Настройки → Из файла (client.json), затем Загрузить .conf — свой файл.")
    return 0


def cmd_list() -> int:
    peers = load_peers()
    if not peers:
        print(f"Пока нет клиентов в {CLIENTS_DIR}")
        return 0
    print(f"{CLIENTS_DIR}")
    for peer in peers:
        print(f"  {peer.get('name')}  {peer.get('address')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AmneziaWG-клиенты в creds/awg/")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ensure", help="создать первого клиента, если нет")
    add_p = sub.add_parser("add", help="добавить клиентов")
    add_p.add_argument("names", nargs="*", help="имена (phone laptop …)")
    add_p.add_argument("--count", type=int, default=0, help="сколько новых ключей")
    sub.add_parser("emit", help="перезаписать .conf и client.json")
    sub.add_parser("list", help="показать клиентов")
    args = parser.parse_args(argv)
    if args.cmd == "ensure":
        peers = ensure_first_client()
        print(f"{len(peers)} клиент(ов) в {CLIENTS_DIR}")
        return 0
    if args.cmd == "add":
        created = add_clients(list(args.names or []), count=args.count)
        for peer in created:
            print(f"+ {peer['name']}  {peer['address']}")
        return emit(apply=True)
    if args.cmd == "emit":
        return emit(apply=True)
    if args.cmd == "list":
        return cmd_list()
    return 1


if __name__ == "__main__":
    sys.exit(main())
