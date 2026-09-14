"""Config load/save/import used by the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desktop.config_io import (
    apply_config,
    ensure_config_defaults,
    load_config,
    merge_imported_config,
    save_config,
)
from desktop.paths import Paths


class SettingsService:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def load(self) -> dict[str, Any]:
        if not self.paths.config_path.is_file():
            return ensure_config_defaults({})
        return load_config(self.paths.config_path)

    def save(self, cfg: dict[str, Any]) -> dict[str, Any]:
        save_config(self.paths.config_path, cfg)
        return apply_config(self.paths.config_path, force=True)

    def merge_import(self, incoming: dict[str, Any]) -> dict[str, Any]:
        base = self.load() if self.paths.config_path.is_file() else None
        return merge_imported_config(base, incoming)

    def apply_runtime(self) -> dict[str, Any]:
        if not self.paths.config_path.is_file():
            return {}
        return apply_config(self.paths.config_path, env_path=self.paths.env_path)
