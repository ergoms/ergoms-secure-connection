#!/usr/bin/env python3
"""Write one client config.json (Reality + Hysteria2 + AmneziaWG) from credentials.env."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

STATE = Path("/var/lib/ops-content-singbox")
CREDS = STATE / "credentials.env"
OUT = STATE / "client.json"
SERVER_CFG = Path("/etc/sing-box/config.json")


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


def _hy2_from_server() -> dict[str, str]:
    """Pull live Hysteria2 inbound from sing-box if credentials.env is incomplete."""
    if not SERVER_CFG.is_file():
        return {}
    try:
        data = json.loads(SERVER_CFG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    for inbound in data.get("inbounds") or []:
        if not isinstance(inbound, dict) or inbound.get("type") != "hysteria2":
            continue
        users = inbound.get("users") if isinstance(inbound.get("users"), list) else []
        pw = ""
        if users and isinstance(users[0], dict):
            pw = str(users[0].get("password") or "").strip()
        tls = inbound.get("tls") if isinstance(inbound.get("tls"), dict) else {}
        obfs = inbound.get("obfs") if isinstance(inbound.get("obfs"), dict) else {}
        port = inbound.get("listen_port") or inbound.get("port") or ""
        return {
            "password": pw,
            "port": str(port or ""),
            "server_name": str(tls.get("server_name") or "").strip(),
            "obfs": str(obfs.get("password") or "").strip(),
        }
    return {}


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


def main() -> int:
    creds = _env_file(CREDS)
    host = _public_ip()
    awg_priv = creds.get("AWG_CLIENT_PRIVATE") or ""
    awg_pub = creds.get("AWG_SERVER_PUBLIC") or ""
    hy2_srv = _hy2_from_server()
    hy2_pw = creds.get("HY2_PASSWORD") or hy2_srv.get("password") or ""
    hy2_port = creds.get("HY2_PORT") or hy2_srv.get("port") or "8443"
    hy2_sni = creds.get("HY2_SERVER_NAME") or hy2_srv.get("server_name") or "www.microsoft.com"
    hy2_obfs = creds.get("HY2_OBFS") or hy2_srv.get("obfs") or ""
    if awg_priv and awg_pub:
        dial = "amneziawg"
    elif hy2_pw:
        dial = "hysteria2"
    else:
        dial = "vless-reality"
    cfg = {
        "corporate": False,
        "use_proxy": False,
        "corporate_proxy": "",
        "socks_scope": "full",
        "http_bridge_port": 1088,
        "pac_listen_port": 1089,
        "watchdog": True,
        "watchdog_interval": 15,
        "watchdog_max_retries": 5,
        "kill_switch": True,
        "git_proxy": False,
        "docker_proxy": False,
        "server": {
            "host": host,
            "port": 443,
            "local_socks_port": 1080,
        },
        "proxy_bypass": ["*.local", "*.lan"],
        "proxy_bypass_via": "direct",
        "tun": {
            "enabled": True,
            "elevate": True,
            "sing_box_path": "",
            "mtu": 1500,
        },
        "transport": {
            "type": "vless-reality",
            "dial": dial,
            "uuid": creds.get("UUID") or "",
            "public_key": creds.get("PUBLIC_KEY") or "",
            "short_id": creds.get("SHORT_ID") or "",
            "server_name": creds.get("SERVER_NAME") or "www.cloudflare.com",
            "port": 443,
            "hysteria2": {
                "password": hy2_pw,
                "port": int(hy2_port or "8443"),
                "server_name": hy2_sni,
                "obfs_password": hy2_obfs,
                "insecure": True,
            },
            "amneziawg": {
                "port": int(creds.get("AWG_PORT") or "51820"),
                "private_key": awg_priv,
                "peer_public_key": awg_pub,
                "pre_shared_key": creds.get("AWG_PSK") or "",
                "address": creds.get("AWG_ADDRESS_CLIENT") or "10.66.66.2/32",
                "mtu": 1280,
                "jc": int(creds.get("AWG_JC") or "0"),
                "jmin": int(creds.get("AWG_JMIN") or "0"),
                "jmax": int(creds.get("AWG_JMAX") or "0"),
                "s1": int(creds.get("AWG_S1") or "0"),
                "s2": int(creds.get("AWG_S2") or "0"),
                "h1": creds.get("AWG_H1") or "",
                "h2": creds.get("AWG_H2") or "",
                "h3": creds.get("AWG_H3") or "",
                "h4": creds.get("AWG_H4") or "",
                "keepalive": 25,
            },
        },
        "reverse_ssh": {
            "enabled": True,
            "vps_user": "root",
            "vps_port": 22,
            "listen_port": 2222,
            "local_port": 22,
            "identity_file": "",
        },
    }
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(OUT, 0o600)
    except OSError:
        pass
    print(f"\n--- client config.json ({OUT}) ---")
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    print("\nСкачайте этот файл на ПК и в клиенте: Настройки → Из файла.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
