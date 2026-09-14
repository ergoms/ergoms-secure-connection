"""Tiny TCP helpers shared by client, sing-box, kill switch, and watchdog."""

from __future__ import annotations

import socket


def port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
