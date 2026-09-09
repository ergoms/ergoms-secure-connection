"""Qt Quick VPN GUI + system tray for ops-content."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from desktop.paths import bundle_dir


def run_gui() -> None:
    try:
        from PySide6.QtCore import QUrl
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

    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    try:
        from PySide6.QtQuickControls2 import QQuickStyle

        QQuickStyle.setStyle("Material")
    except Exception:
        pass

    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    QQuickWindow.setDefaultAlphaBuffer(True)

    app = QApplication(sys.argv)
    app.setApplicationName("ERGOMS VPN")
    app.setOrganizationName("ERGOMS")
    app.setQuitOnLastWindowClosed(False)

    ico_path = _icon_path()
    icon = QIcon(str(ico_path)) if ico_path else _fallback_icon()
    app.setWindowIcon(icon)

    from desktop.ui.bridge import GuiBridge, qml_dir

    bridge = GuiBridge()
    engine = QQmlApplicationEngine()
    qml_root = qml_dir()
    engine.addImportPath(str(qml_root))
    engine.rootContext().setContextProperty("bridge", bridge)
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

    tray = _setup_tray(app, icon, bridge)
    bridge.quitRequested.connect(app.quit)
    if getattr(bridge, "startHidden", False):
        window.setProperty("visible", False)

    raise SystemExit(app.exec())


def _icon_path() -> Path | None:
    candidates = (
        Path(__file__).with_name("app_icon.ico"),
        bundle_dir() / "desktop" / "app_icon.ico",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _fallback_icon() -> QIcon:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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


def _setup_tray(app: QApplication, icon: QIcon, bridge: object) -> QSystemTrayIcon:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("ERGOMS VPN")
    menu = QMenu()

    def add(label: str, slot: object) -> None:
        action = QAction(label, menu)
        action.triggered.connect(slot)
        menu.addAction(action)

    add("Открыть", bridge.showWindow)  # type: ignore[attr-defined]
    menu.addSeparator()
    add("Подключить", bridge.enableConnection)  # type: ignore[attr-defined]
    add("Отключить", bridge.disableConnection)  # type: ignore[attr-defined]
    add("TUN вкл", bridge.enableTun)  # type: ignore[attr-defined]
    add("TUN выкл", bridge.disableTun)  # type: ignore[attr-defined]
    menu.addSeparator()
    add("Выход", bridge.quitApp)  # type: ignore[attr-defined]

    tray.setContextMenu(menu)

    def _activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            bridge.showWindow()  # type: ignore[attr-defined]

    tray.activated.connect(_activated)
    tray.show()
    return tray


if __name__ == "__main__":
    run_gui()
