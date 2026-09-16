#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

route = json.loads(Path("/etc/sing-box/config.json").read_text()).get("route") or {}
print("route_ok", any(
    r.get("port") == [21114, 21115, 21116, 21117, 21118, 21119]
    and r.get("outbound") == "direct"
    for r in route.get("rules") or []
))
subprocess.run(["/usr/local/bin/sing-box", "check", "-c", "/etc/sing-box/config.json"], check=True)
subprocess.run(["systemctl", "restart", "sing-box"], check=True)
print("sing-box", subprocess.check_output(["systemctl", "is-active", "sing-box"], text=True).strip())
