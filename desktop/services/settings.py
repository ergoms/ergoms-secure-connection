"""Config load / save / import used by the GUI."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop.config.model import default_config_template, ensure_config_defaults
from desktop.config_crypto import MAGIC, decrypt_config
from desktop.config_io import (
    apply_config,
    install_amnezia_conf,
    load_config,
    looks_like_wg_conf,
    merge_imported_config,
    migrate_legacy_awg_json,
    read_awg_source_name,
    save_config,
)
from desktop.paths import Paths
from desktop.ui.settings_map import apply_settings_to_cfg


@dataclass
class ImportResult:
    kind: str
    extra: str = ""
    same_live: bool = False


def awg_import_note(migrated: bool, incoming: Any, conf_path: Path) -> str:
    if migrated:
        return " AmneziaWG сохранён в amneziawg.conf."
    if _json_has_awg(incoming) and not conf_path.is_file():
        return " AWG из JSON пропущен — загрузите .conf."
    return ""


def _json_has_awg(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("amneziawg"), dict) and data["amneziawg"]:
        return True
    tr = data.get("transport")
    return isinstance(tr, dict) and isinstance(tr.get("amneziawg"), dict) and bool(
        tr.get("amneziawg")
    )


class SettingsService:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def load(self) -> dict[str, Any]:
        if not self.paths.config_path.is_file():
            return ensure_config_defaults({})
        return load_config(self.paths.config_path)

    def awg_source_name(self) -> str:
        return read_awg_source_name(self.paths.config_path)

    def write_from_map(
        self, get_value: Callable[[str], object], *, corporate: bool
    ) -> dict[str, Any]:
        if self.paths.config_path.is_file():
            cfg = load_config(self.paths.config_path)
        else:
            cfg = default_config_template()
        apply_settings_to_cfg(cfg, get_value, corporate=corporate)
        save_config(self.paths.config_path, cfg)
        apply_config(self.paths.config_path, force=True)
        return load_config(self.paths.config_path, force=True)

    def commit_imported(self, cfg: dict[str, Any]) -> dict[str, Any]:
        cfg = ensure_config_defaults(cfg)
        self.paths.ensure_dirs()
        save_config(self.paths.config_path, cfg)
        apply_config(self.paths.config_path, force=True)
        return load_config(self.paths.config_path, force=True)

    def import_awg_text(self, text: str, *, source_name: str = "") -> None:
        install_amnezia_conf(self.paths.config_path, text, source_name=source_name)
        apply_config(self.paths.config_path, force=True)

    def import_path(
        self, src: Path, *, password: str | None = None
    ) -> ImportResult:
        raw = src.read_bytes()
        encrypted = raw.startswith(MAGIC) or src.suffix.lower() == ".enc"
        existing = (
            load_config(self.paths.config_path)
            if self.paths.config_path.is_file()
            else default_config_template()
        )
        if encrypted:
            if not password:
                raise ValueError("Нужен пароль к файлу")
            incoming = decrypt_config(raw, password)
            migrated = migrate_legacy_awg_json(incoming, self.paths.awg_conf_path)
            cfg = merge_imported_config(existing, incoming)
            extra = awg_import_note(migrated, incoming, self.paths.awg_conf_path)
            self.commit_imported(cfg)
            return ImportResult(kind="json", extra=extra)
        text = raw.decode("utf-8-sig")
        if looks_like_wg_conf(text) or src.suffix.lower() == ".conf":
            self.import_awg_text(text, source_name=src.name)
            return ImportResult(kind="awg")
        try:
            same_live = src.resolve() == self.paths.config_path.resolve()
        except OSError:
            same_live = False
        if same_live:
            apply_config(self.paths.config_path, force=True)
            return ImportResult(kind="live", same_live=True)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Файл не JSON-объект")
        migrated = migrate_legacy_awg_json(data, self.paths.awg_conf_path)
        cfg = merge_imported_config(existing, data)
        extra = awg_import_note(migrated, data, self.paths.awg_conf_path)
        self.commit_imported(cfg)
        return ImportResult(kind="json", extra=extra)

    def export_to(self, dest: Path) -> None:
        if self.paths.config_path.is_file():
            cfg = ensure_config_defaults(load_config(self.paths.config_path))
        else:
            cfg = default_config_template()
        if dest.suffix.lower() != ".json":
            dest = dest.with_suffix(".json")
        save_config(dest, cfg)
