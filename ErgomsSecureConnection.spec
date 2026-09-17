# -*- mode: python ; coding: utf-8 -*-
"""One-dir GUI/CLI build → dist/ERGOMS SECURE CONNECTION/ (exe + _internal)."""

import sys
from pathlib import Path

root = Path(SPECPATH).resolve()

if sys.platform == "win32":
    sb = root / "tools" / "ergoms-tun.exe"
    if not sb.is_file():
        sb = root / "tools" / "sing-box.exe"
else:
    sb = root / "tools" / "sing-box"
if not sb.is_file():
    raise SystemExit(
        "sing-box missing in tools/ — run: python -m desktop download-sing-box"
    )
awg = root / "tools" / ("ergoms-tun-awg.exe" if sys.platform == "win32" else "sing-box-awg")
if not awg.is_file():
    raise SystemExit(
        f"{awg.name} missing in tools/ — run: python -m desktop download-sing-box-awg"
    )
awg_ver = root / "tools" / "sing-box-awg.ver"

ico = root / "desktop" / "app_icon.ico"
datas = [
    (str(root / "desktop" / "ui" / "qml"), "desktop/ui/qml"),
    (str(root / "lib"), "lib"),
]
if ico.is_file():
    datas.append((str(ico), "desktop"))
ico_ergoms = root / "desktop" / "app_icon_ergoms.ico"
if ico_ergoms.is_file():
    datas.append((str(ico_ergoms), "desktop"))
png_ergoms = root / "desktop" / "app_icon_ergoms.png"
if png_ergoms.is_file():
    datas.append((str(png_ergoms), "desktop"))
png_dark = root / "desktop" / "app_icon.png"
if png_dark.is_file():
    datas.append((str(png_dark), "desktop"))
if awg_ver.is_file():
    datas.append((str(awg_ver), "tools"))

a = Analysis(
    [str(root / "desktop" / "entry.py")],
    pathex=[str(root)],
    binaries=[(str(sb), "tools"), (str(awg), "tools")],
    datas=datas,
    hiddenimports=[
        "desktop",
        "desktop.branding",
        "desktop.gui",
        "desktop.ui",
        "desktop.ui.bridge",
        "desktop.ui.theme",
        "desktop.client",
        "desktop.reverse_ssh",
        "desktop.autostart",
        "desktop.elevate",
        "desktop.pac_serve",
        "desktop.__main__",
        "desktop.linux_service",
        "lib.connect",
        "lib.connect_socks",
        "lib.connect_proxy",
        "lib.pac",
        "lib.netutil",
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

_exe_kw = {}
if sys.platform == "win32":
    _ver = root / "installer" / "file_version_info.txt"
    if _ver.is_file():
        _exe_kw["version"] = str(_ver)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ERGOMS SECURE CONNECTION",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ico) if ico.is_file() else None,
    **_exe_kw,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ERGOMS SECURE CONNECTION",
)
