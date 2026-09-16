"""QObject bridge: QML ↔ OpsClient (background workers, status, settings)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    Property,
    QMetaObject,
    QObject,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
    Q_ARG,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlPropertyMap
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit

from desktop import __version__
from desktop import autostart
from desktop import procutil
from desktop.branding import ENV_RESUME, env
from desktop.client import OpsClient
from desktop.config_crypto import MAGIC, decrypt_config
from desktop.config_io import (
    apply_config,
    config_is_ready,
    default_config_template,
    ensure_config_defaults,
    get_tun_enabled,
    infer_corporate,
    install_amnezia_conf,
    load_config,
    looks_like_wg_conf,
    merge_imported_config,
    migrate_legacy_awg_json,
    read_awg_source_name,
    save_config,
)
from desktop.paths import Paths, gui_command
from desktop.services.connection import ConnectionService
from desktop.services.elevation import ElevationService
from desktop.services.settings import SettingsService
from desktop.services.status import C_MUTED, present_status
from desktop.proc_net import list_processes, list_services
from desktop.route_analyzer import analyze_process, analyze_service, analyze_token
from desktop.route_tokens import token_payload
from desktop.ui.settings_map import (
    apply_mode_to_settings,
    apply_settings_to_cfg,
    cfg_to_settings,
    settings_defaults,
)

LogFn = Callable[[str], None]

_C_MUTED = C_MUTED
_C_ACCENT = "#2dd4a8"
_C_OK = "#2dd4a8"
_C_WARN = "#e6c07b"
_C_DANGER = "#f07178"


def _scope_label(scope: str) -> str:
    return {"full": "Всё", "github": "GitHub"}.get(scope, scope or "—")


def _json_has_awg(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("amneziawg"), dict) and data["amneziawg"]:
        return True
    tr = data.get("transport")
    return isinstance(tr, dict) and isinstance(tr.get("amneziawg"), dict) and bool(
        tr.get("amneziawg")
    )


def _analyze_spec(spec: str) -> dict[str, Any]:
    raw = (spec or "").strip()
    if raw.lower().startswith("svc:"):
        return analyze_service(raw)
    if raw.lower().startswith("pid:"):
        try:
            pid = int(raw.split(":", 1)[1])
        except ValueError:
            pid = 0
        return analyze_process(pid=pid)
    path = ""
    name = raw
    if raw.lower().startswith("exe:"):
        name = raw[4:].strip()
    if "\\" in name or "/" in name:
        path = name
    return analyze_process(name=name, path=path)


def _awg_import_note(migrated: bool, incoming: Any, conf_path: Path) -> str:
    if migrated:
        return " AmneziaWG сохранён в amneziawg.conf."
    if _json_has_awg(incoming) and not conf_path.is_file():
        return " AWG из JSON пропущен — загрузите .conf."
    return ""


class _BgTask(QRunnable):
    def __init__(self, fn: Callable[[], None]) -> None:
        super().__init__()
        self._fn = fn
        self.setAutoDelete(True)

    def run(self) -> None:
        self._fn()


class GuiBridge(QObject):
    """In-process façade for the QML shell. Does not change OpsClient."""

    toast = Signal(str, str)  # message, kind: info|error|warn
    logAppended = Signal(str)
    showRequested = Signal()
    hideRequested = Signal()
    closingUi = Signal()
    quitRequested = Signal()

    activeChanged = Signal()
    busyChanged = Signal()
    busyTextChanged = Signal()
    statusTitleChanged = Signal()
    statusSubChanged = Signal()
    statusColorChanged = Signal()
    logTextChanged = Signal()
    pageChanged = Signal()
    powerTextChanged = Signal()
    canReconnectChanged = Signal()
    corporateChanged = Signal()
    autostartChanged = Signal()
    analyzeReady = Signal(str, str)
    processListReady = Signal(str)
    serviceListReady = Signal(str)
    peersReady = Signal(str, str)
    executablePicked = Signal(str)

    _bgFinished = Signal(str)
    _statusReady = Signal(object, bool)
    _statusFailed = Signal(str)
    _schedulePoll = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = Paths()
        self.paths.ensure_dirs()
        if self.paths.config_path.is_file():
            apply_config(self.paths.config_path, env_path=self.paths.env_path)

        self._log_lines: list[str] = []
        self.client = OpsClient(
            paths=self.paths,
            log=self._enqueue_log,
            startup_cleanup=False,
            inprocess_helpers=True,
        )
        self.settings_svc = SettingsService(self.paths)
        self.connection = ConnectionService(self.client)
        self.elevation = ElevationService(self.client)

        self._active = False
        self._tun = False
        self._kill_switch_on = False
        self._busy = False
        self._busy_text = ""
        self._status_title = "Отключено"
        self._status_sub = ""
        self._status_color = _C_MUTED
        self._server_target = "—"
        self._scope = "—"
        self._mode_label = "VPN"
        self._page = "home"
        self._socks_port = 1080
        self._http_port = 1088
        self._pac_port = 1089
        self._socks_up = False
        self._http_up = False
        self._pac_up = False
        self._watchdog_up = False
        self._reverse_ssh_up = False
        self._reverse_ssh_port = 2222
        self._singbox_up = False
        self._power_text = "Подключить"
        self._can_reconnect = False
        self._tun_button_text = "TUN вкл"
        self._config_ready = False
        self._corporate = False
        self._autostart = autostart.is_enabled()
        self._start_hidden = False
        self._last_status_sig = ""
        self._status_busy = False
        self._closing = False
        self._overrides_cleared = False
        self._await_status = False
        self._busy_intent = ""
        self._status_pending = False
        self._status_pending_force = False

        self._settings = QQmlPropertyMap(self)
        for key, value in settings_defaults().items():
            self._settings.insert(key, value)

        self._bgFinished.connect(self._on_bg_finished)
        self._statusReady.connect(self._on_status_ready)
        self._statusFailed.connect(self._apply_status_error)

        if not self.paths.config_path.is_file():
            try:
                self.client.init()
                self._enqueue_log("Создан config.json")
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log(f"инициализация: {exc}")

        self._enqueue_log(f"ERGOMS SECURE CONNECTION {__version__}")
        self._enqueue_log(f"данные: {self.paths.root}")
        self._enqueue_log(f"журнал: {self.paths.logs_dir / 'ergoms-secure-connection.log'}")

        self.loadSettings()
        self._sync_config_ready()

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._on_poll_tick)
        self._schedulePoll.connect(self._status_timer.start)
        QTimer.singleShot(300, self._on_poll_tick)
        resume = env(ENV_RESUME).lower()
        if autostart.launched_from_autostart():
            self._start_hidden = True
        if resume == "on":
            QTimer.singleShot(400, self.enableConnection)
        elif resume == "off":
            QTimer.singleShot(400, self.disableConnection)
        elif resume == "tun-on":
            QTimer.singleShot(400, self.enableTun)
        elif resume == "tun-off":
            QTimer.singleShot(400, self.disableTun)
        elif autostart.launched_from_autostart() and self._config_ready:
            QTimer.singleShot(600, self.enableConnection)

    # ── properties ──────────────────────────────────────────────────────

    @Property(bool, notify=activeChanged)
    def active(self) -> bool:
        return self._active

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=busyTextChanged)
    def busyText(self) -> str:
        return self._busy_text

    @Property(str, notify=statusTitleChanged)
    def statusTitle(self) -> str:
        return self._status_title

    @Property(str, notify=statusSubChanged)
    def statusSub(self) -> str:
        return self._status_sub

    @Property(str, notify=statusColorChanged)
    def statusColor(self) -> str:
        return self._status_color

    @Property(str, notify=logTextChanged)
    def logText(self) -> str:
        return "\n".join(self._log_lines)

    @Property(str, notify=pageChanged)
    def page(self) -> str:
        return self._page

    @Property(str, notify=powerTextChanged)
    def powerText(self) -> str:
        return self._power_text

    @Property(bool, notify=canReconnectChanged)
    def canReconnect(self) -> bool:
        return self._can_reconnect

    @Property(bool, notify=corporateChanged)
    def corporate(self) -> bool:
        return self._corporate

    @Property(bool, notify=autostartChanged)
    def autostart(self) -> bool:
        return self._autostart

    @Property(bool, constant=True)
    def startHidden(self) -> bool:
        return self._start_hidden

    @Property(str, constant=True)
    def dataRoot(self) -> str:
        return str(self.paths.root)

    @Property(QObject, constant=True)
    def settings(self) -> QQmlPropertyMap:
        return self._settings

    # ── slots ───────────────────────────────────────────────────────────

    @Slot(str)
    def setPage(self, page: str) -> None:
        if page not in ("home", "settings", "exceptions", "log") or page == self._page:
            return
        self._page = page
        self.pageChanged.emit()

    @Slot(str, result=str)
    def tokenKind(self, raw: str) -> str:
        return str(token_payload(raw).get("kind") or "unknown")

    @Slot(str, result=str)
    def tokenLabel(self, raw: str) -> str:
        return str(token_payload(raw).get("label") or raw)

    @Slot(str, result=bool)
    def tokenShared(self, raw: str) -> bool:
        return bool(token_payload(raw).get("shared"))

    @Slot(str, result=bool)
    def tokenAmbiguous(self, raw: str) -> bool:
        return bool(token_payload(raw).get("ambiguous"))

    @Slot(str)
    def analyzeToken(self, query: str) -> None:
        text = (query or "").strip()

        def work() -> None:
            try:
                result = analyze_token(text)
            except Exception as exc:  # noqa: BLE001
                result = {"query": text, "kind": "unknown", "warning": str(exc)}
            self.analyzeReady.emit(text, json.dumps(result, ensure_ascii=False))

        self._spawn(work)

    @Slot()
    def listProcesses(self) -> None:
        def work() -> None:
            try:
                items = [p.as_dict() for p in list_processes()]
            except Exception:  # noqa: BLE001
                items = []
            self.processListReady.emit(json.dumps(items, ensure_ascii=False))

        self._spawn(work)

    @Slot()
    def listServices(self) -> None:
        def work() -> None:
            try:
                items = [s.as_dict() for s in list_services()]
            except Exception:  # noqa: BLE001
                items = []
            self.serviceListReady.emit(json.dumps(items, ensure_ascii=False))

        self._spawn(work)

    @Slot(str)
    def collectProcessPeers(self, spec: str) -> None:
        raw = (spec or "").strip()

        def work() -> None:
            try:
                result = _analyze_spec(raw)
            except Exception as exc:  # noqa: BLE001
                result = {"query": raw, "kind": "process", "warning": str(exc), "peers": []}
            self.peersReady.emit(raw, json.dumps(result, ensure_ascii=False))

        self._spawn(work)

    @Slot()
    def browseExecutable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Исполняемый файл",
            "",
            "Programs (*.exe);;All files (*)",
        )
        if path:
            self.executablePicked.emit(path)

    @Slot()
    def saveExceptions(self) -> None:
        try:
            self._write_settings_to_disk()
            self._enqueue_log("Правила сохранены")
            if self._active:
                self.toast.emit("Сохранено. Применится при следующем подключении.", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    def _handoff_if_needed(self, action: str) -> bool:
        """Relaunch elevated once. True = caller must stop (handoff or cancel)."""
        if not self.elevation.needed(action):
            return False
        flags = [f"--{action}"]
        if action == "on":
            flags = ["--connect"]
        elif action == "off":
            flags = ["--disconnect"]
        if self._start_hidden:
            flags.append("--autostart")
        ok = self.elevation.relaunch(
            gui_command(*flags),
            cwd=str(self.paths.root),
        )
        if ok:
            os._exit(0)
        self.toast.emit(
            "Нужны права администратора один раз — потом окна Windows больше не появятся.",
            "error",
        )
        return True

    @Slot()
    def toggleConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready and not self._active and not self._kill_switch_on:
            self.toast.emit("Загрузите конфиг", "warn")
            return
        if self._active or self._kill_switch_on:
            if self._handoff_if_needed("off"):
                return
            self._run_bg(self.connection.disable, waiting="Отключение…")
        else:
            if self._handoff_if_needed("on"):
                return
            self._run_bg(self.connection.enable, waiting="Подключение…")

    @Slot()
    def enableConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready:
            self.toast.emit("Загрузите конфиг", "warn")
            return
        if self._handoff_if_needed("on"):
            return
        self._run_bg(self.connection.enable, waiting="Подключение…")

    @Slot()
    def disableConnection(self) -> None:
        if self._busy:
            return
        if self._handoff_if_needed("off"):
            return
        self._run_bg(self.connection.disable, waiting="Отключение…")

    @Slot()
    def reconnectConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready and not self._active and not self._kill_switch_on:
            self.toast.emit("Загрузите конфиг", "warn")
            return
        if self._handoff_if_needed("on"):
            return
        self._run_bg(self.connection.reconnect, waiting="Переподключение…")

    @Slot()
    def toggleTun(self) -> None:
        if self._busy:
            return
        if self._tun:
            self.disableTun()
        else:
            self.enableTun()

    @Slot()
    def enableTun(self) -> None:
        if self._busy:
            return
        if self._handoff_if_needed("tun-on"):
            return
        self._run_bg(self.connection.enable_tun, waiting="Включаю TUN…")

    @Slot()
    def disableTun(self) -> None:
        if self._busy:
            return
        if self._handoff_if_needed("tun-off"):
            return
        self._run_bg(self.connection.disable_tun, waiting="Выключаю TUN…")

    @Slot()
    def loadSettings(self) -> None:
        if not self.paths.config_path.is_file():
            self._sync_config_ready()
            return
        cfg = load_config(self.paths.config_path)
        self._apply_cfg_to_settings(cfg)

    def _set_corporate(self, on: bool) -> None:
        self._settings.insert("corporate", on)
        if on != self._corporate:
            self._corporate = on
            self.corporateChanged.emit()

    @Slot(bool)
    def setAutostart(self, on: bool) -> None:
        if self._busy or self._active:
            return
        try:
            if on:
                autostart.enable()
            else:
                autostart.disable()
            self._autostart = autostart.is_enabled()
            self.autostartChanged.emit()
            self.toast.emit(
                "Автозапуск включён" if self._autostart else "Автозапуск выключен",
                "info",
            )
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    def _apply_cfg_to_settings(self, cfg: dict[str, Any]) -> None:
        self._set_corporate(infer_corporate(cfg))
        self._mode_label = "Корпоративный" if self._corporate else "VPN"
        for key, value in cfg_to_settings(cfg).items():
            self._settings.insert(key, value)
        loaded = bool(self._settings.value("awgLoaded"))
        source = read_awg_source_name(self.paths.config_path)
        if loaded:
            self._settings.insert("awgSummary", source or "amneziawg.conf")
        self._sync_config_ready()

    def _write_settings_to_disk(self) -> None:
        if self.paths.config_path.is_file():
            cfg = load_config(self.paths.config_path)
        else:
            cfg = default_config_template()
        apply_settings_to_cfg(
            cfg, self._settings.value, corporate=bool(self._corporate)
        )
        save_config(self.paths.config_path, cfg)
        apply_config(self.paths.config_path, force=True)
        self._apply_cfg_to_settings(load_config(self.paths.config_path, force=True))

    @Slot(bool)
    def applyCorporateMode(self, on: bool) -> None:
        if self._busy or self._active:
            return
        self._set_corporate(on)
        apply_mode_to_settings(
            self._settings.value, self._settings.insert, corporate=on
        )
        self._mode_label = "Корпоративный" if on else "VPN"
        try:
            self._write_settings_to_disk()
            self._enqueue_log(
                "режим: корпоративный" if on else "режим: обычный VPN"
            )
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    @Slot()
    def importConfigFile(self) -> None:
        if self._busy or self._active:
            self.toast.emit("Дождитесь окончания операции или отключите VPN", "warn")
            return
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Конфиг ERGOMS SECURE CONNECTION",
            "",
            "Config (*.json *.enc *.conf);;JSON (*.json);;AmneziaWG (*.conf);;Encrypted (*.enc);;All files (*)",
        )
        if not path:
            return
        try:
            self._import_config_path(Path(path))
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    def _import_config_path(self, src: Path) -> None:
        raw = src.read_bytes()
        encrypted = raw.startswith(MAGIC) or src.suffix.lower() == ".enc"
        existing = (
            load_config(self.paths.config_path)
            if self.paths.config_path.is_file()
            else default_config_template()
        )
        if encrypted:
            password, ok = QInputDialog.getText(
                None,
                "ERGOMS SECURE CONNECTION",
                "Пароль к файлу:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not password:
                return
            incoming = decrypt_config(raw, password)
            migrated = migrate_legacy_awg_json(incoming, self.paths.awg_conf_path)
            cfg = merge_imported_config(existing, incoming)
            extra = _awg_import_note(migrated, incoming, self.paths.awg_conf_path)
            self._commit_imported_cfg(cfg, extra=extra)
            return
        text = raw.decode("utf-8-sig")
        if looks_like_wg_conf(text) or src.suffix.lower() == ".conf":
            self._import_awg_conf_text(text, source_name=src.name)
            return
        try:
            same_live = src.resolve() == self.paths.config_path.resolve()
        except OSError:
            same_live = False
        if same_live:
            apply_config(self.paths.config_path, force=True)
            self.loadSettings()
            self._enqueue_log("Конфиг загружен")
            self.toast.emit("Конфиг загружен", "info")
            self._refresh_status(force=True)
            return
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Файл не JSON-объект")
        migrated = migrate_legacy_awg_json(data, self.paths.awg_conf_path)
        cfg = merge_imported_config(existing, data)
        extra = _awg_import_note(migrated, data, self.paths.awg_conf_path)
        self._commit_imported_cfg(cfg, extra=extra)

    @Slot()
    def importAwgConfFile(self) -> None:
        if self._busy or self._active:
            self.toast.emit("Дождитесь окончания операции или отключите VPN", "warn")
            return
        path, _ = QFileDialog.getOpenFileName(
            None,
            "AmneziaWG .conf",
            "",
            "AmneziaWG (*.conf);;All files (*)",
        )
        if not path:
            return
        try:
            src = Path(path)
            self._import_awg_conf_text(src.read_text(encoding="utf-8-sig"), source_name=src.name)
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    def _import_awg_conf_text(self, text: str, *, source_name: str = "") -> None:
        install_amnezia_conf(self.paths.config_path, text, source_name=source_name)
        apply_config(self.paths.config_path, force=True)
        self.loadSettings()
        self._enqueue_log("AmneziaWG .conf загружен")
        self.toast.emit("AmneziaWG .conf загружен", "info")
        self._refresh_status(force=True)

    def _commit_imported_cfg(self, cfg: dict[str, Any], *, extra: str = "") -> None:
        cfg = ensure_config_defaults(cfg)
        self.paths.ensure_dirs()
        save_config(self.paths.config_path, cfg)
        apply_config(self.paths.config_path, force=True)
        self.loadSettings()
        self._enqueue_log("Конфиг загружен")
        self.toast.emit("Конфиг загружен" + extra, "info")
        self._refresh_status(force=True)

    @Slot()
    def exportConfigFile(self) -> None:
        if self.paths.config_path.is_file():
            cfg = ensure_config_defaults(load_config(self.paths.config_path))
        else:
            cfg = default_config_template()
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Сохранить config.json",
            "config.json",
            "JSON (*.json);;All files (*)",
        )
        if not path:
            return
        dest = Path(path)
        if dest.suffix.lower() != ".json":
            dest = dest.with_suffix(".json")
        try:
            save_config(dest, cfg)
            self._enqueue_log(f"Копия конфига → {dest}")
            self.toast.emit("JSON сохранён. AmneziaWG — отдельный .conf", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    def _sync_config_ready(self) -> None:
        try:
            cfg = (
                load_config(self.paths.config_path)
                if self.paths.config_path.is_file()
                else None
            )
        except (OSError, json.JSONDecodeError, ValueError):
            cfg = None
        ready = config_is_ready(cfg)
        if ready != self._config_ready:
            self._config_ready = ready
        if not ready and not self._active and not self._busy:
            self._status_title = "Нет конфига"
            self._status_sub = "Загрузите конфиг"
            self._power_text = "Подключить"
            self._can_reconnect = False
            self.statusTitleChanged.emit()
            self.statusSubChanged.emit()
            self.powerTextChanged.emit()
            self.canReconnectChanged.emit()

    @Slot()
    def saveSettings(self) -> None:
        if self._busy or self._active:
            self.toast.emit("Дождитесь окончания операции или отключите VPN", "warn")
            return
        try:
            self._write_settings_to_disk()
            self._enqueue_log("Настройки сохранены")
            self.toast.emit(
                "Сохранено.\nЕсли туннель был включён — выключите и включите снова.",
                "info",
            )
            self._sync_config_ready()
            self._refresh_status(force=True)
        except Exception as exc:  # noqa: BLE001
            self.toast.emit(str(exc), "error")

    @Slot()
    def copyLog(self) -> None:
        text = "\n".join(self._log_lines)
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            self.toast.emit("Буфер обмена недоступен", "error")
            return
        clipboard.setText(text)
        self.toast.emit("Журнал скопирован", "info")

    @Slot()
    def hideWindow(self) -> None:
        self.hideRequested.emit()

    @Slot()
    def showWindow(self) -> None:
        self.showRequested.emit()

    @Slot()
    def teardownNow(self) -> None:
        """AboutToQuit leftover undo. No-op if quitApp already owns shutdown."""
        if self._closing:
            return
        try:
            self.client.teardown_overrides_if_dirty()
        except Exception:  # noqa: BLE001
            pass

    @Slot()
    def quitApp(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._status_timer.stop()
        self.hideRequested.emit()
        self.closingUi.emit()

        def work() -> None:
            try:
                self.client.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.quitRequested.emit()

        self._spawn(work)

    # ── internals ───────────────────────────────────────────────────────

    def _spawn(self, fn: Callable[[], None]) -> None:
        QThreadPool.globalInstance().start(_BgTask(fn))

    def _enqueue_log(self, msg: str) -> None:
        app = QGuiApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            QMetaObject.invokeMethod(
                self, "_append_log", Qt.QueuedConnection, Q_ARG(str, msg)
            )
            return
        self._append_log(msg)

    @Slot(str)
    def _append_log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp}  {msg}"
        self._log_lines.append(line)
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-1500:]
        self.logAppended.emit(line)
        self.logTextChanged.emit()

    def _set_busy(self, busy: bool, waiting: str = "Подождите…") -> None:
        self._busy = busy
        self._busy_text = waiting if busy else ""
        self.busyChanged.emit()
        self.busyTextChanged.emit()

    def _run_bg(self, fn: Callable[[], None], waiting: str = "Подождите…") -> None:
        if self._busy:
            return
        wait = (waiting or "").lower()
        if "отключ" in wait:
            self._busy_intent = "off"
        elif "подключ" in wait:
            self._busy_intent = "on"
        else:
            self._busy_intent = ""
        self._await_status = True
        self._set_busy(True, waiting)

        def work() -> None:
            err = ""
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                self._enqueue_log(f"ошибка: {exc}")
            self._bgFinished.emit(err)

        self._spawn(work)

    @Slot(str)
    def _on_bg_finished(self, err: str) -> None:
        waiting = self._busy_text
        if err:
            self._await_status = False
            self._busy_intent = ""
            self._set_busy(False)
            self.toast.emit(err, "error")
            self._refresh_status(force=True)
            return
        wait = (waiting or "").lower()
        if "подключ" not in wait:
            self._apply_optimistic(waiting)
        self._refresh_status(force=True)
        if "подключ" in wait or "включаю tun" in wait:
            from desktop.leak_shield import consume_browser_toast

            try:
                if consume_browser_toast(self.paths.var_dir):
                    self.toast.emit(
                        "Перезапустите браузер, чтобы трафик шёл через VPN",
                        "info",
                    )
            except Exception:  # noqa: BLE001
                pass

    def _apply_optimistic(self, waiting: str) -> None:
        wait = (waiting or "").lower()
        if "отключ" in wait:
            self._apply_status(
                {
                    "singbox_running": False,
                    "tun_running": False,
                    "socks_up": False,
                    "http_up": False,
                    "pac_up": False,
                    "kill_switch": False,
                    "kill_switch_applied": False,
                    "socks_scope": "full" if self._scope == "Всё" else "github",
                    "server_target": self._server_target,
                    "watchdog_running": False,
                    "reverse_ssh_running": self._reverse_ssh_up,
                    "reverse_ssh_listen": self._reverse_ssh_port,
                    "socks_port": self._socks_port,
                    "http_port": self._http_port,
                    "pac_port": self._pac_port,
                },
                force=True,
            )
        elif "подключ" in wait:
            self._apply_status(
                {
                    "singbox_running": True,
                    "tun_running": True if get_tun_enabled() else self._tun,
                    "socks_up": True,
                    "http_up": True,
                    "pac_up": True,
                    "kill_switch": self._kill_switch_on,
                    "kill_switch_applied": self._kill_switch_on,
                    "socks_scope": "full" if self._scope == "Всё" else "github",
                    "server_target": self._server_target,
                    "watchdog_running": True,
                    "reverse_ssh_running": self._reverse_ssh_up,
                    "reverse_ssh_listen": self._reverse_ssh_port,
                    "socks_port": self._socks_port,
                    "http_port": self._http_port,
                    "pac_port": self._pac_port,
                },
                force=True,
            )

    def _finish_await_status(self) -> None:
        if not self._await_status:
            return
        self._await_status = False
        self._busy_intent = ""
        self._set_busy(False)

    @Slot()
    def _on_poll_tick(self) -> None:
        self._refresh_status(force=False)

    def _refresh_status(self, force: bool = False) -> None:
        if self._closing:
            return
        if self._status_busy:
            self._status_pending = True
            if force:
                self._status_pending_force = True
            return

        def work() -> None:
            self._status_busy = True
            try:
                want_force = force
                while True:
                    force_now = want_force or self._status_pending_force
                    self._status_pending = False
                    self._status_pending_force = False
                    try:
                        st = self.client.status(include_git=False)
                        err = ""
                        if (
                            isinstance(st, dict)
                            and not self._overrides_cleared
                            and not (st.get("singbox_running") or st.get("tun_running"))
                        ):
                            self.client.teardown_overrides_if_dirty()
                            self._overrides_cleared = True
                    except Exception as exc:  # noqa: BLE001
                        st = None
                        err = str(exc)
                    if self._closing:
                        return
                    if err:
                        self._statusFailed.emit(err)
                    elif st is not None:
                        self._statusReady.emit(st, force_now)
                    if not self._status_pending:
                        break
                    want_force = True
            finally:
                self._status_busy = False
            delay = 5000 if self._page == "home" and not self._busy else 10000
            if not self._closing:
                self._schedulePoll.emit(delay)

        self._spawn(work)

    @Slot(object, bool)
    def _on_status_ready(self, st: object, force: bool) -> None:
        if isinstance(st, dict):
            self._apply_status(st, force=force or self._await_status)

    @Slot(str)
    def _apply_status_error(self, err: str) -> None:
        if self._busy and not self._await_status:
            return
        self._status_title = "Ошибка"
        self._status_sub = err[:80]
        self._status_color = _C_DANGER
        self._can_reconnect = bool(self._active or self._kill_switch_on)
        self.statusTitleChanged.emit()
        self.statusSubChanged.emit()
        self.statusColorChanged.emit()
        self.canReconnectChanged.emit()
        self._finish_await_status()

    def _apply_status(self, st: dict[str, Any], *, force: bool = False) -> None:
        singbox = bool(st.get("singbox_running"))
        tun = bool(st.get("tun_running"))
        tun_wanted = bool(st.get("tun_wanted", tun))
        tun_ready = bool(st.get("tun_ready")) if "tun_ready" in st else (not tun_wanted or tun)
        connecting = bool(st.get("connecting"))
        socks_up = bool(st.get("socks_up")) if "socks_up" in st else singbox
        http_up = bool(st.get("http_up")) if "http_up" in st else singbox
        pac_up = bool(st.get("pac_up")) if "pac_up" in st else singbox
        active = bool(singbox or tun)
        scope = _scope_label(str(st.get("socks_scope") or ""))
        target = str(st.get("server_target") or st.get("ssh_target") or "—")
        ks_on = bool(st.get("kill_switch_applied") or (active and st.get("kill_switch")))
        probe_err = str(st.get("exit_probe_error") or "")
        probe_hint = str(st.get("exit_probe_hint") or "")
        sig = (
            f"{singbox}|{tun}|{active}|{socks_up}|{http_up}|{pac_up}|{scope}|{target}"
            f"|{st.get('watchdog_running')}|{st.get('reverse_ssh_running')}"
            f"|{st.get('reverse_ssh_listen')}|{ks_on}|{probe_err}|{probe_hint}"
            f"|{connecting}|{tun_ready}|{tun_wanted}"
        )
        if not force and (sig == self._last_status_sig or (self._busy and not self._await_status)):
            return
        if self._await_status and self._busy_intent == "on" and not (singbox or tun):
            return
        if (
            self._await_status
            and self._busy_intent == "on"
            and (connecting or (tun_wanted and not tun_ready))
            and not probe_err
        ):
            return
        if self._await_status and self._busy_intent == "off" and (singbox or tun):
            return
        self._last_status_sig = sig
        view = present_status(st, config_ready=self._config_ready, corporate=self._corporate)
        self._active = view.active
        self._tun = view.tun
        self._kill_switch_on = view.kill_switch_on
        self._singbox_up = view.singbox_up
        if view.active:
            self._overrides_cleared = False
        self._watchdog_up = view.watchdog_up
        self._reverse_ssh_up = view.reverse_ssh_up
        self._reverse_ssh_port = view.reverse_ssh_port
        self._socks_up = view.socks_up
        self._http_up = view.http_up
        self._pac_up = view.pac_up
        self._socks_port = view.socks_port
        self._http_port = view.http_port
        self._pac_port = view.pac_port
        self._server_target = view.server_target
        self._scope = view.scope
        self._mode_label = "Корпоративный" if self._corporate else "VPN"
        self._tun_button_text = view.tun_button_text
        if self.paths.config_path.is_file():
            try:
                cfg = self.settings_svc.load()
                server = cfg.get("server") or {}
                self._socks_port = int(server.get("local_socks_port") or self._socks_port)
            except Exception:  # noqa: BLE001
                pass
        if view.toast and probe_err != getattr(self, "_last_probe_toast", None):
            self._last_probe_toast = probe_err
            self.toast.emit(view.toast, "error")
        elif not probe_err:
            self._last_probe_toast = None
        self._status_title = view.title
        self._status_sub = view.subtitle
        self._status_color = view.color
        self._power_text = view.power_text
        self._can_reconnect = bool(view.can_reconnect)

        self.activeChanged.emit()
        self.statusTitleChanged.emit()
        self.statusSubChanged.emit()
        self.statusColorChanged.emit()
        self.powerTextChanged.emit()
        self.canReconnectChanged.emit()
        if self._await_status:
            if self._busy_intent == "on" and (singbox or tun):
                if connecting or (tun_wanted and not tun_ready and not probe_err):
                    return
                self._finish_await_status()
            elif self._busy_intent == "off" and not singbox and not tun:
                self._finish_await_status()
            elif self._busy_intent not in ("on", "off"):
                self._finish_await_status()


def qml_dir() -> Path:
    here = Path(__file__).resolve().parent / "qml"
    if here.is_dir():
        return here
    from desktop.paths import bundle_dir

    bundled = bundle_dir() / "desktop" / "ui" / "qml"
    return bundled
