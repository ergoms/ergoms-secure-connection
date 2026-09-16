"""Unified sing-box JSON builder (full TUN profile and AWG handshake-only)."""

from __future__ import annotations

from typing import Any

from desktop.singbox.manager import SingboxModeManager


def build_singbox_config(manager: SingboxModeManager, **kwargs: Any) -> dict[str, Any]:
    return manager.build_config(**kwargs)
