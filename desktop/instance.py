"""Single GUI instance: second launch shows the existing window."""

from __future__ import annotations

import sys
from typing import Callable

from desktop.branding import APP_IPC

def _invalid_handle() -> int:
    import ctypes

    return int(ctypes.c_void_p(-1).value or 0)


def activate_existing() -> bool:
    """True if another GUI is running and was asked to show."""
    if sys.platform == "win32":
        return _activate_win()
    return False


def listen(on_show: Callable[[], None]) -> object | None:
    """Accept 'show' from later launches. Keep the returned server alive."""
    try:
        from PySide6.QtNetwork import QLocalServer
    except ImportError:
        return None

    QLocalServer.removeServer(APP_IPC)
    server = QLocalServer()
    opts = getattr(QLocalServer, "WorldAccessOption", None)
    if opts is None:
        sock_opt = getattr(QLocalServer, "SocketOption", None)
        opts = getattr(sock_opt, "WorldAccessOption", None) if sock_opt else None
    if opts is not None:
        try:
            server.setSocketOptions(opts)
        except Exception:
            pass

    def _accept() -> None:
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.close()
        on_show()

    server.newConnection.connect(_accept)
    if not server.listen(APP_IPC):
        return None
    return server


def _activate_win() -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    pipe = rf"\\.\pipe\{APP_IPC}"
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    handle = kernel32.CreateFileW(
        pipe,
        GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle in (0, -1) or handle == _invalid_handle():
        return False
    try:
        try:
            user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        payload = b"show\n"
        written = wintypes.DWORD(0)
        kernel32.WriteFile(
            handle, payload, len(payload), ctypes.byref(written), None
        )
    finally:
        kernel32.CloseHandle(handle)
    return True
