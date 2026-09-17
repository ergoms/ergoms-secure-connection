"""GUI palettes, status-color mapping, and theme icon files."""

from __future__ import annotations

from desktop.services.status import C_ACCENT, C_DANGER, C_MUTED, C_WARN
from desktop.ui.theme import (
    THEME_DARK,
    THEME_ERGOMS,
    apply_theme_map,
    icon_path,
    normalize_theme_id,
    palette,
    qml_icon_path,
    status_color,
    status_role,
    theme_tokens,
)


def test_normalize_theme_id() -> None:
    assert normalize_theme_id("ergoms") == THEME_ERGOMS
    assert normalize_theme_id("DARK") == THEME_DARK
    assert normalize_theme_id("nope") == THEME_DARK
    assert normalize_theme_id(None) == THEME_DARK


def test_ergoms_palette_matches_core_client() -> None:
    pal = palette(THEME_ERGOMS)
    assert pal["bg"] == "#f2f2f2"
    assert pal["surface"] == "#ffffff"
    assert pal["text"] == "#101223"
    assert pal["muted"] == "#6e6e6e"
    assert pal["accent"] == "#d0322d"
    assert pal["accentText"] == "#ffffff"
    assert pal["light"] is True


def test_dark_palette_keeps_mint() -> None:
    pal = palette(THEME_DARK)
    assert pal["bg"] == "#0c1017"
    assert pal["accent"] == "#2dd4a8"
    assert pal["accentText"] == "#04140f"
    assert pal["light"] is False


def test_status_hex_maps_to_theme_tokens() -> None:
    assert status_role(C_MUTED) == "muted"
    assert status_role(C_ACCENT) == "accent"
    assert status_role(C_WARN) == "warn"
    assert status_role(C_DANGER) == "danger"
    assert status_color(THEME_DARK, C_ACCENT) == "#2dd4a8"
    assert status_color(THEME_ERGOMS, C_ACCENT) == "#d0322d"
    assert status_color(THEME_ERGOMS, C_DANGER) == "#dc3545"
    assert status_color(THEME_ERGOMS, "muted") == "#6e6e6e"
    assert status_color(THEME_ERGOMS, "unknown") == "#6e6e6e"


def test_theme_tokens_and_map() -> None:
    tokens = theme_tokens(THEME_ERGOMS)
    assert tokens["name"] == THEME_ERGOMS
    assert tokens["iconUrl"]
    assert tokens["otherIconUrl"]
    assert tokens["iconUrl"] != tokens["otherIconUrl"]

    filled: dict[str, object] = {}

    class Map:
        def insert(self, key: str, value: object) -> None:
            filled[key] = value

    apply_theme_map(Map(), THEME_DARK)
    assert filled["name"] == THEME_DARK
    assert filled["accent"] == "#2dd4a8"


def test_qml_theme_tokens_are_defined() -> None:
    import re
    from pathlib import Path

    qml_dir = Path(__file__).resolve().parents[1] / "desktop" / "ui" / "qml"
    used: set[str] = set()
    for path in qml_dir.glob("*.qml"):
        used.update(re.findall(r"\bT\.([A-Za-z]+)", path.read_text(encoding="utf-8")))
    defined = set(theme_tokens(THEME_DARK))
    missing = used - defined
    assert not missing, f"QML uses unknown theme tokens: {sorted(missing)}"


def test_icon_files_exist_for_both_themes() -> None:
    dark_ico = icon_path(THEME_DARK)
    ergoms_ico = icon_path(THEME_ERGOMS)
    assert dark_ico is not None and dark_ico.is_file()
    assert ergoms_ico is not None and ergoms_ico.is_file()
    assert dark_ico.name != ergoms_ico.name
    dark_img = qml_icon_path(THEME_DARK)
    ergoms_img = qml_icon_path(THEME_ERGOMS)
    assert dark_img is not None and dark_img.is_file()
    assert ergoms_img is not None and ergoms_img.is_file()
