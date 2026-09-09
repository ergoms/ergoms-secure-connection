# -*- mode: python ; coding: utf-8 -*-
"""One-file GUI/CLI build → dist/OpsContent.exe (Windows) / dist/OpsContent (Linux)."""

import sys
from pathlib import Path

root = Path(SPECPATH).resolve()

sb = root / "tools" / ("sing-box.exe" if sys.platform == "win32" else "sing-box")
if not sb.is_file():
    raise SystemExit(
        f"{sb.name} missing in tools/ — run: python -m desktop download-sing-box"
    )

ico = root / "desktop" / "app_icon.ico"
datas = [
    (str(root / "desktop" / "ui" / "qml"), "desktop/ui/qml"),
    (str(root / "lib"), "lib"),
]
if ico.is_file():
    datas.append((str(ico), "desktop"))

a = Analysis(
    [str(root / "desktop" / "entry.py")],
    pathex=[str(root)],
    binaries=[(str(sb), "tools")],
    datas=datas,
    hiddenimports=[
        "desktop",
        "desktop.gui",
        "desktop.ui",
        "desktop.ui.bridge",
        "desktop.client",
        "desktop.reverse_ssh",
        "desktop.autostart",
        "desktop.__main__",
        "lib.connect_socks",
        "lib.connect_proxy",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtNetwork",
        "PySide6.QtOpenGL",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtPdf",
        "PySide6.QtBluetooth",
        "PySide6.QtMultimedia",
        "PySide6.QtLocation",
        "PySide6.QtTextToSpeech",
    ],
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
    icon=str(ico) if ico.is_file() else None,
)
