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
        "bg": "#f2f2f2",
        "surface": "#ffffff",
        "surface2": "#f1f1f1",
        "btn": "#e1e1e1",
        "btnHover": "#d4d4d4",
        "border": "#e0e0e0",
        "text": "#101223",
        "muted": "#6e6e6e",
        "accent": "#d0322d",
        "accentDim": "#b02b27",
        "accentGlow": "#d0322d55",
        "accentText": "#ffffff",
        "accentSoft": "#29d0322d",
        "danger": "#dc3545",
        "dangerDim": "#b02a37",
        "dangerSoft": "#29dc3545",
        "warn": "#ffab00",
        "ok": "#4caf50",
        "select": "#f3c5c3",
        "windowBorder": "#e0e0e0",
        "shadow": "#00000022",
        "hairline": "#14000000",
        "rowBg": "#0a000000",
        "scroll": "#b0b0b0",
        "scrollHover": "#8a8a8a",
        "scrollPress": "#6e6e6e",
        "toastError": "#f8e6e6",
        "toastWarn": "#fff4d6",
        "bannerUpdate": "#f8e6e5",
        "bannerUpdateBorder": "#59d0322d",
        "dim": "#66000000",
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
