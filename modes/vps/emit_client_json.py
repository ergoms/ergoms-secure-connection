#!/usr/bin/env python3
"""Write one client config.json (Reality + AmneziaWG) from credentials.env."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from desktop.config_io import default_config_template  # noqa: E402

STATE = Path("/var/lib/ops-content-singbox")
CREDS = STATE / "credentials.env"
OUT = STATE / "client.json"


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


def main() -> int:
    creds = _env_file(CREDS)
    host = _public_ip()
    awg_priv = creds.get("AWG_CLIENT_PRIVATE") or ""
    awg_pub = creds.get("AWG_SERVER_PUBLIC") or ""
    dial = "amneziawg" if awg_priv and awg_pub else "vless-reality"
    cfg = deepcopy(default_config_template())
    cfg["server"]["host"] = host
    cfg["transport"]["dial"] = dial
    cfg["transport"]["uuid"] = creds.get("UUID") or ""
    cfg["transport"]["public_key"] = creds.get("PUBLIC_KEY") or ""
    cfg["transport"]["short_id"] = creds.get("SHORT_ID") or ""
    cfg["transport"]["server_name"] = creds.get("SERVER_NAME") or "www.cloudflare.com"
    tr = cfg.get("transport")
    if isinstance(tr, dict):
        tr.pop("amneziawg", None)
        tr.pop("hysteria2", None)
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(OUT, 0o600)
    except OSError:
        pass
    conf_path = STATE / "amneziawg.conf"
    if awg_priv and awg_pub:
        from desktop.config_io import render_amnezia_conf

        conf_text = render_amnezia_conf(
            {
                "private_key": awg_priv,
                "peer_public_key": awg_pub,
                "pre_shared_key": creds.get("AWG_PSK") or "",
                "address": creds.get("AWG_ADDRESS_CLIENT") or "10.66.66.2/32",
                "port": int(creds.get("AWG_PORT") or "51820"),
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
        conf_path.write_text(conf_text, encoding="utf-8")
        try:
            os.chmod(conf_path, 0o600)
        except OSError:
            pass
    print(f"\n--- client config.json ({OUT}) ---")
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    if awg_priv and awg_pub:
        print(f"\n--- AmneziaWG .conf ({conf_path}) ---")
        print(conf_path.read_text(encoding="utf-8"))
        print("Скачайте config.json и amneziawg.conf. AWG в клиенте: Настройки → Загрузить .conf.")
    else:
        print("\nСкачайте этот файл на ПК и в клиенте: Настройки → Из файла.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
