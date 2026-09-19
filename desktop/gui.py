"""Qt Quick VPN GUI + system tray for ERGOMS SECURE CONNECTION."""

from __future__ import annotations

import os
import sys

from desktop.branding import APP_AUMID, APP_NAME, ORG_NAME
from desktop.paths import Paths
from desktop.ui.theme import THEME_ERGOMS, icon_path


def run_gui() -> None:
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.mime=false")
    try:
        from PySide6.QtCore import QUrl, QtMsgType, qInstallMessageHandler
        from PySide6.QtGui import QIcon, QSurfaceFormat
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "GUI: установите PySide6 — poetry install --extras gui",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    data = Paths()
    data.ensure_dirs()
    qml_cache = data.var_dir / "qmlcache"
    qml_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QML_DISK_CACHE_PATH", str(qml_cache))
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    try:
        from PySide6.QtQuickControls2 import QQuickStyle

        QQuickStyle.setStyle("Material")
    except Exception:
        pass

    def _qt_message(mode: object, _ctx: object, message: str) -> None:
        text = str(message)
        if "clipboard" in text.lower() or text.startswith("qt.qpa.mime"):
            return
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            print(text, file=sys.stderr)

    qInstallMessageHandler(_qt_message)

    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    QQuickWindow.setDefaultAlphaBuffer(True)

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_AUMID)  # type: ignore[attr-defined]
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(False)

    from desktop.instance import listen
    from desktop.ui.bridge import GuiBridge, qml_dir

    bridge = GuiBridge()
    app._ergoms_ipc = listen(bridge.showWindow)  # noqa: SLF001 — keep server alive

    icon = _window_icon(bridge.uiTheme)
    app.setWindowIcon(icon)

    engine = QQmlApplicationEngine()
    qml_root = qml_dir()
    engine.addImportPath(str(qml_root))
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("T", bridge.themeTokens)
    engine.warnings.connect(lambda ws: [print(w.toString(), file=sys.stderr) for w in ws])
    main_qml = qml_root / "Main.qml"
    if not main_qml.is_file():
        print(f"GUI: не найден {main_qml}", file=sys.stderr)
        raise SystemExit(1)
    engine.load(QUrl.fromLocalFile(str(main_qml.resolve())))
    if not engine.rootObjects():
        print("GUI: не удалось загрузить QML", file=sys.stderr)
        raise SystemExit(1)

    window = engine.rootObjects()[0]
    _round_corners(window)
    _apply_window_icon(window, icon)

    tray = _setup_tray(app, icon, bridge, window)
    bridge.closingUi.connect(tray.hide)
    bridge.quitRequested.connect(app.quit)
    app.aboutToQuit.connect(bridge.teardownNow)

    def _sync_icons() -> None:
        next_icon = _window_icon(bridge.uiTheme)
        app.setWindowIcon(next_icon)
        _apply_window_icon(window, next_icon)
        tray.setIcon(next_icon)

    bridge.uiThemeChanged.connect(_sync_icons)
    if getattr(bridge, "startHidden", False):
        window.setProperty("visible", False)

    raise SystemExit(app.exec())


def _window_icon(theme_id: str) -> object:
    from PySide6.QtGui import QIcon

    ico = icon_path(theme_id)
    if ico is not None:
        return QIcon(str(ico))
    return _fallback_icon(theme_id)


def _apply_window_icon(window: object, icon: object) -> None:
    setter = getattr(window, "setIcon", None)
    if callable(setter):
        setter(icon)


def _fallback_icon(theme_id: str = "") -> object:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if theme_id == THEME_ERGOMS:
        painter.setBrush(QColor(242, 242, 242))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
        red = QColor(208, 50, 45)
        pen = QPen(red)
        pen.setWidth(5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(14, 14, 36, 36))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red)
        painter.drawEllipse(28, 28, 8, 8)
    else:
        painter.setBrush(QColor(12, 16, 23))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
        mint = QColor(45, 212, 168)
        pen = QPen(mint)
        pen.setWidth(6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(16, 16, 32, 32), 115 * 16, 310 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mint)
        painter.drawEllipse(28, 28, 8, 8)
    painter.end()
    return QIcon(pix)


def _round_corners(window: object) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.winId())  # type: ignore[attr-defined]
        dwmwa_window_corner_preference = 33
        dwmwcp_round = 2
        value = ctypes.c_int(dwmwcp_round)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd,
            dwmwa_window_corner_preference,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        pass


def _window_is_open(window: object) -> bool:
    from PySide6.QtGui import QWindow

    if not bool(window.isVisible()):  # type: ignore[attr-defined]
        return False
    return window.visibility() != QWindow.Visibility.Minimized  # type: ignore[attr-defined]


def _setup_tray(app: QApplication, icon: QIcon, bridge: object, window: object) -> QSystemTrayIcon:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip(APP_NAME)
    menu = QMenu()

    def add(label: str, slot: object) -> None:
        action = QAction(label, menu)
        action.triggered.connect(slot)
        menu.addAction(action)

    toggle = QAction(str(getattr(bridge, "powerText", "Подключить")), menu)
    toggle.triggered.connect(bridge.activatePower)  # type: ignore[attr-defined]
    menu.addAction(toggle)
    menu.addSeparator()
    add("Выход", bridge.quitApp)  # type: ignore[attr-defined]

    def _sync_toggle() -> None:
        busy = bool(getattr(bridge, "busy", False))
        busy_text = str(getattr(bridge, "busyText", "") or "")
        power = str(getattr(bridge, "powerText", "Подключить") or "Подключить")
        toggle.setEnabled(not busy)
        toggle.setText(busy_text if busy and busy_text else power)

    def _sync_tip() -> None:
        title = str(getattr(bridge, "statusTitle", "") or "")
        tray.setToolTip(f"{APP_NAME} — {title}" if title else APP_NAME)

    for sig in (
        "powerTextChanged",
        "busyChanged",
        "busyTextChanged",
        "statusTitleChanged",
        "canReconnectChanged",
    ):
        signal = getattr(bridge, sig, None)
        if signal is None:
            continue
        if sig == "statusTitleChanged":
            signal.connect(_sync_tip)
        else:
            signal.connect(_sync_toggle)
    _sync_toggle()
    _sync_tip()

    tray.setContextMenu(menu)

    def _activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return
        # Не смотрим isActive(): клик по трею уже снимает фокус с окна.
        if _window_is_open(window):
            bridge.hideWindow()  # type: ignore[attr-defined]
        else:
            bridge.showWindow()  # type: ignore[attr-defined]

    tray.activated.connect(_activated)
    tray.show()
    return tray


if __name__ == "__main__":
    run_gui()
