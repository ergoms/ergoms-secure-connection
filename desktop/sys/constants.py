"""Shared OS / proxy constants."""

from __future__ import annotations

SINGBOX_PROCESS_NAMES = (
    "sing-box.exe",
    "sing-box",
    "sing-box-awg",
    "ergoms-tun.exe",
    "ergoms-tun",
    "ergoms-tun-awg.exe",
    "ergoms-tun-awg",
)

DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1"
DOCKER_DESKTOP_HOST_GATEWAY = "192.168.65.254"
DOCKER_NO_PROXY = (
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
    "192.168.65.0/24",
    DOCKER_DESKTOP_HOST_GATEWAY,
    "10.0.0.0/8",
    "172.16.0.0/12",
)
