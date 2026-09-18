"""GUI appearance: dark mint (current) and ERGO MS red-white palettes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desktop.branding import APP_NAME, ORG_NAME
from desktop.paths import bundle_dir

THEME_DARK = "dark"
THEME_ERGOMS = "ergoms"
DEFAULT_THEME = THEME_DARK
THEME_IDS = (THEME_DARK, THEME_ERGOMS)

_SETTINGS_KEY = "uiTheme"

# present_status() still emits the dark-mint hex values; map them to roles.
STATUS_ROLE_BY_HEX = {
    "#8b95a8": "muted",
    "#2dd4a8": "accent",
    "#e6c07b": "warn",
    "#f07178": "danger",
}

FONTS = {
    "fontUi": "Segoe UI",
    "fontUiFallback": "Segoe UI",
    "fontMono": "Consolas",
}

PALETTES: dict[str, dict[str, Any]] = {
    THEME_DARK: {
        "bg": "#0c1017",
        "surface": "#151b27",
        "surface2": "#1c2433",
        "btn": "#2a3448",
        "btnHover": "#364257",
        "border": "#2f3a4e",
        "text": "#eef1f6",
        "muted": "#8b95a8",
        "accent": "#2dd4a8",
        "accentDim": "#1a9e7a",
        "accentGlow": "#2dd4a855",
        "accentText": "#04140f",
        "accentSoft": "#292dd4a8",
        "danger": "#f07178",
        "dangerDim": "#c45c63",
        "dangerSoft": "#29f07178",
        "warn": "#e6c07b",
        "ok": "#2dd4a8",
        "select": "#1a5c4a",
        "windowBorder": "#2a3348",
        "shadow": "#00000088",
        "hairline": "#0dffffff",
        "rowBg": "#08ffffff",
        "scroll": "#5c6678",
        "scrollHover": "#9aa3b4",
        "scrollPress": "#d5dbe6",
        "toastError": "#3a1d24",
        "toastWarn": "#3a3220",
        "bannerUpdate": "#16352d",
        "bannerUpdateBorder": "#592dd4a8",
        "dim": "#85000000",
        "light": False,
        **FONTS,
    },
    THEME_ERGOMS: {
        # Warm paper + clearer brick red (readable when controls are disabled).
        "bg": "#f4efe9",
        "surface": "#fbf7f3",
        "surface2": "#efe6df",
        "btn": "#e7dcd4",
        "btnHover": "#dccfc6",
        "border": "#e0d3cb",
        "text": "#2c2422",
        "muted": "#7d726c",
        "accent": "#d03a34",
        "accentDim": "#b42f2a",
        "accentGlow": "#d03a3433",
        "accentText": "#fffaf7",
        "accentSoft": "#f3d4d2",
        "danger": "#8a3036",
        "dangerDim": "#6f262c",
        "dangerSoft": "#ead6d6",
        "warn": "#b08948",
        "ok": "#5b8f72",
        "select": "#f0d0cd",
        "windowBorder": "#d7cbc3",
        "shadow": "#3a201818",
        "hairline": "#242c2422",
        "rowBg": "#122c2422",
        "scroll": "#c4b8b1",
        "scrollHover": "#a3968f",
        "scrollPress": "#7d726c",
        "toastError": "#f3e4e2",
        "toastWarn": "#f3ead4",
        "bannerUpdate": "#f3e4e2",
        "bannerUpdateBorder": "#40d03a34",
        "dim": "#592c2422",
        "light": True,
        **FONTS,
    },
}


def normalize_theme_id(name: str | None) -> str:
    raw = (name or "").strip().lower()
    if raw in THEME_IDS:
        return raw
    return DEFAULT_THEME


def palette(theme_id: str | None) -> dict[str, Any]:
    return dict(PALETTES[normalize_theme_id(theme_id)])


def status_role(hex_color: str) -> str:
    return STATUS_ROLE_BY_HEX.get((hex_color or "").lower(), "muted")


def status_color(theme_id: str | None, hex_or_role: str) -> str:
    pal = palette(theme_id)
    key = (hex_or_role or "").lower()
    role = STATUS_ROLE_BY_HEX.get(key, key if key in pal else "muted")
    if role == "accent":
        return str(pal["accent"])
    return str(pal.get(role, pal["muted"]))


def _desktop_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def icon_filename(theme_id: str | None, *, png: bool = False) -> str:
    ergoms = normalize_theme_id(theme_id) == THEME_ERGOMS
    if png:
        return "app_icon_ergoms.png" if ergoms else "app_icon.png"
    return "app_icon_ergoms.ico" if ergoms else "app_icon.ico"


def _first_existing(*names: str) -> Path | None:
    folders = (_desktop_dir(), bundle_dir() / "desktop")
    for name in names:
        for folder in folders:
            path = folder / name
            if path.is_file():
                return path
    return None


def icon_path(theme_id: str | None) -> Path | None:
    """Window / tray ICO (falls back to PNG)."""
    return _first_existing(icon_filename(theme_id), icon_filename(theme_id, png=True))


def qml_icon_path(theme_id: str | None) -> Path | None:
    """Title-bar image prefers PNG."""
    return _first_existing(icon_filename(theme_id, png=True), icon_filename(theme_id))


def icon_url(theme_id: str | None) -> str:
    path = qml_icon_path(theme_id)
    if path is None:
        return ""
    return path.resolve().as_uri()


def theme_tokens(theme_id: str | None) -> dict[str, Any]:
    name = normalize_theme_id(theme_id)
    other = THEME_ERGOMS if name == THEME_DARK else THEME_DARK
    tokens = palette(name)
    tokens["name"] = name
    tokens["iconUrl"] = icon_url(name)
    tokens["otherIconUrl"] = icon_url(other)
    return tokens


def apply_theme_map(target: Any, theme_id: str | None) -> str:
    """Fill a QQmlPropertyMap (or anything with insert())."""
    name = normalize_theme_id(theme_id)
    for key, value in theme_tokens(name).items():
        target.insert(key, value)
    return name


def load_ui_theme() -> str:
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        return DEFAULT_THEME
    raw = QSettings(ORG_NAME, APP_NAME).value(_SETTINGS_KEY, DEFAULT_THEME)
    return normalize_theme_id(str(raw or ""))


def save_ui_theme(theme_id: str) -> str:
    name = normalize_theme_id(theme_id)
    try:
        from PySide6.QtCore import QSettings

        QSettings(ORG_NAME, APP_NAME).setValue(_SETTINGS_KEY, name)
    except ImportError:
        pass
    return name
