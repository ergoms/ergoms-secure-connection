# -*- mode: python ; coding: utf-8 -*-
"""One-file GUI/CLI build → build/OpsContent.exe"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve()

datas, binaries, hiddenimports = collect_all("PySide6")

sb = root / "tools" / "sing-box.exe"
if not sb.is_file():
    raise SystemExit(
        "tools/sing-box.exe missing — run: python -m desktop download-sing-box"
    )
binaries += [(str(sb), "tools")]
datas += [
    (str(root / "desktop" / "ui" / "qml"), "desktop/ui/qml"),
    (str(root / "desktop" / "app_icon.ico"), "desktop"),
    (str(root / "lib"), "lib"),
]
hiddenimports += [
    "desktop",
    "desktop.gui",
    "desktop.ui",
    "desktop.ui.bridge",
    "desktop.client",
    "desktop.__main__",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
]

a = Analysis(
    [str(root / "desktop" / "entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OpsContent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "desktop" / "app_icon.ico"),
)
