"""Copy text to the OS clipboard. Qt on Windows often fails while the buffer is locked."""

from __future__ import annotations

import sys
import time


def copy_text(text: str, *, attempts: int = 8) -> bool:
    """Put *text* on the clipboard. Retries, then a Win32 fallback."""
    if _copy_via_qt(text, attempts=attempts):
        return True
    if sys.platform == "win32":
        return _copy_via_win32(text, attempts=attempts)
    return False


def _copy_via_qt(text: str, *, attempts: int) -> bool:
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import QCoreApplication
    except ImportError:
        return False
    app = QGuiApplication.instance()
    if app is None:
        return False
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return False
    for _ in range(max(1, attempts)):
        try:
            clipboard.setText(text)
        except Exception:  # noqa: BLE001
            time.sleep(0.05)
            continue
        QCoreApplication.processEvents()
        if clipboard.text() == text:
            return True
        time.sleep(0.05)
    return False


def _copy_via_win32(text: str, *, attempts: int) -> bool:
    """OpenClipboard/SetClipboardData — works when Qt's viewer chain is stuck."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    payload = (text + "\x00").encode("utf-16-le")
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    for _ in range(max(1, attempts)):
        if not user32.OpenClipboard(None):
            time.sleep(0.05)
            continue
        handle = None
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
            if not handle:
                return False
            locked = kernel32.GlobalLock(handle)
            if not locked:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(locked, payload, len(payload))
            kernel32.GlobalUnlock(handle)
            if user32.SetClipboardData(CF_UNICODETEXT, handle):
                handle = None
                return True
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)
        time.sleep(0.05)
    return False
