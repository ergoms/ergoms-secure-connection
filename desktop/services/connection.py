"""Connect / disconnect / TUN operations."""

from __future__ import annotations

from typing import Any


class ConnectionService:
    def __init__(self, client: Any) -> None:
        self.client = client

    def enable(self) -> None:
        self.client.enable()

    def disable(self) -> None:
        self.client.disable()

    def enable_tun(self) -> None:
        self.client.enable_tun()

    def disable_tun(self) -> None:
        self.client.disable_tun()

    def status(self, *, include_git: bool = False) -> dict[str, Any]:
        return self.client.status(include_git=include_git)
