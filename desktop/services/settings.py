"""Config load used by the GUI."""

from __future__ import annotations

from typing import Any

from desktop.config_io import (
    ensure_config_defaults,
    load_config,
)
from desktop.paths import Paths


class SettingsService:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def load(self) -> dict[str, Any]:
        if not self.paths.config_path.is_file():
            return ensure_config_defaults({})
        return load_config(self.paths.config_path)
