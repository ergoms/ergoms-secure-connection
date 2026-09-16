#!/usr/bin/env python3
"""Skip VLESS sniff on RustDesk ports and force hbbs always-relay."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

CONF = Path("/etc/sing-box/config.json")
PORTS = [21114, 21115, 21116, 21117, 21118, 21119]
SKIP = {
    "inbound": ["vless-in"],
    "port": PORTS,
    "outbound": "direct",
}
DROPIN = Path("/etc/systemd/system/rustdesk-hbbs.service.d/always-relay.conf")


def patch_singbox() -> Path:
    bak = CONF.with_name(CONF.name + ".bak-rustdesk-" + time.strftime("%Y%m%d%H%M%S"))
    shutil.copy2(CONF, bak)
    cfg = json.loads(CONF.read_text())
    route = cfg.setdefault("route", {})
    rules = list(route.get("rules") or [])
    rules = [
        r
        for r in rules
        if not (
            r.get("inbound") == ["vless-in"]
            and r.get("port") == PORTS
            and r.get("outbound") == "direct"
        )
    ]
    idx = next(
        (
            i
            for i, r in enumerate(rules)
            if r.get("action") == "sniff" and "vless-in" in (r.get("inbound") or [])
        ),
        0,
    )
    rules.insert(idx, SKIP)
    route["rules"] = rules
    CONF.write_text(json.dumps(cfg, indent=2) + "\n")
    subprocess.run(
        ["/usr/local/bin/sing-box", "check", "-c", str(CONF)], check=True
    )
    return bak


def patch_hbbs() -> None:
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text("[Service]\nEnvironment=ALWAYS_USE_RELAY=Y\n")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "rustdesk-hbbs"], check=True)


def main() -> None:
    bak = patch_singbox()
    patch_hbbs()
    print("backup", bak)
    print("hbbs", subprocess.check_output(["systemctl", "is-active", "rustdesk-hbbs"], text=True).strip())
    print("OK")


if __name__ == "__main__":
    main()
