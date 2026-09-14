"""Canonical defaults and presets. Every other module imports from here."""

from __future__ import annotations

CORPORATE_PROXY_PRESET = "192.0.2.10:3128"
CORPORATE_BYPASS_PRESET = ["*.intranet.example", "*.local", "*.lan"]
STANDARD_BYPASS_PRESET = ["*.local", "*.lan"]

REALITY_DEFAULT_SNI = "www.cloudflare.com"
HY2_DEFAULT_SNI = "www.microsoft.com"
AWG_DEFAULT_PORT = 51820
AWG_DEFAULT_ADDRESS = "10.66.66.2/32"
AWG_DEFAULT_MTU = 1280
TUN_DEFAULT_MTU = 1400
TUN_MTU_MIN = 1280
TUN_MTU_MAX = 1500

HTTP_BRIDGE_PORT = 1088
PAC_LISTEN_PORT = 1089
LOCAL_SOCKS_PORT = 1080
SERVER_PORT = 443
HY2_DEFAULT_PORT = 8443
REVERSE_SSH_LISTEN = 2222
REVERSE_SSH_VPS_PORT = 22
WATCHDOG_INTERVAL = 15
WATCHDOG_MAX_RETRIES = 5

DEFAULT_SERVER_HOST = "YOUR_VPS_IP_OR_HOSTNAME"
DEFAULT_DIAL = "amneziawg"

BLOCKED_HY2_SNI = frozenset(
    {
        "www.cloudflare.com",
        "cloudflare.com",
        "cloudflare-dns.com",
        "1.1.1.1",
        "one.one.one.one",
    }
)

BLOCKED_HOSTS = [
    "github.com",
    "www.github.com",
    "api.github.com",
    "codeload.github.com",
    "ssh.github.com",
    "gist.github.com",
    "ghcr.io",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "github.githubassets.com",
    "avatars.githubusercontent.com",
    "packages.github.com",
    "cursor.com",
    "*.cursor.com",
    "*.cursor.sh",
    "*.cursor-cdn.com",
    "*.cursorapi.com",
    "*.cursorvm.com",
    "downloads.cursor.com",
    "marketplace.cursorapi.com",
    "api2.cursor.sh",
    "api3.cursor.sh",
    "api4.cursor.sh",
    "api5.cursor.sh",
    "authenticate.cursor.sh",
    "authenticator.cursor.sh",
]
