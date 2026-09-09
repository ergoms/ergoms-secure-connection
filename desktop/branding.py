"""Product name and identifiers (ERGOMS VPN)."""

from __future__ import annotations

import os

APP_NAME = "ERGOMS VPN"
APP_ID = "ergoms-vpn"
APP_EXE = "ErgomsVPN"
ORG_NAME = "ERGOMS"

# Env (new names). Old OPS_CONTENT_* still accepted.
ENV_DATA = "ERGOMS_VPN_DATA"
ENV_AUTOSTART = "ERGOMS_VPN_AUTOSTART"
ENV_WATCHDOG_CHILD = "ERGOMS_VPN_WATCHDOG_CHILD"
ENV_GUI = "ERGOMS_VPN_GUI"
ENV_PYTHON = "ERGOMS_VPN_PYTHON"
ENV_SOCKS = "ERGOMS_VPN_SOCKS"
ENV_HTTP_PROXY = "ERGOMS_VPN_HTTP_PROXY"
ENV_CONNECT_ALLOW = "ERGOMS_VPN_CONNECT_ALLOW"
ENV_DOCKER_HOST_IP = "ERGOMS_VPN_DOCKER_HOST_IP"

_LEGACY = {
    ENV_DATA: "OPS_CONTENT_DATA",
    ENV_AUTOSTART: "OPS_CONTENT_AUTOSTART",
    ENV_WATCHDOG_CHILD: "OPS_CONTENT_WATCHDOG_CHILD",
    ENV_GUI: "OPS_CONTENT_GUI",
    ENV_PYTHON: "OPS_CONTENT_PYTHON",
    ENV_SOCKS: "OPS_CONTENT_SOCKS",
    ENV_HTTP_PROXY: "OPS_CONTENT_HTTP_PROXY",
    ENV_CONNECT_ALLOW: "OPS_CONTENT_CONNECT_ALLOW",
    ENV_DOCKER_HOST_IP: "OPS_CONTENT_DOCKER_HOST_IP",
}


def env(name: str, default: str = "") -> str:
    val = (os.environ.get(name) or "").strip()
    if val:
        return val
    legacy = _LEGACY.get(name)
    if legacy:
        val = (os.environ.get(legacy) or "").strip()
        if val:
            return val
    return default
