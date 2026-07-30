# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for OpsContent.exe — run from repo root via build-desktop.ps1

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # SPECPATH = desktop/ → repo root

datas = [
    (str(ROOT / "config" / "config.example.json"), "config"),
    (str(ROOT / "config" / ".env.example"), "config"),
    (str(ROOT / "desktop" / "app_icon.ico"), "desktop"),
]

hiddenimports = [
    "lib",
    "lib.connect_proxy",
    "lib.http_via_socks",
    "lib.probe_connect",
    "desktop",
    "desktop.client",
    "desktop.gui",
    "desktop.bridge",
    "desktop.config_io",
    "desktop.git_proxy",
    "desktop.win_proxy",
    "desktop.paths",
    "pystray._win32",
    "PIL._tkinter_finder",
]

a = Analysis(
    [str(ROOT / "desktop" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OpsContent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "desktop" / "app_icon.ico"),
)
