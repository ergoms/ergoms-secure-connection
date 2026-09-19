"""QObject bridge: QML ↔ OpsClient (background workers, status, settings)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Q_ARG,
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
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlPropertyMap
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit

from desktop import __version__, autostart
from desktop.branding import ENV_RESUME, env
from desktop.client import OpsClient
from desktop.config_io import (
    apply_config,
    config_is_ready,
    infer_corporate,
    load_config,
)
from desktop.lifecycle.actions import Action, action_from_resume
from desktop.paths import Paths, gui_command
from desktop.proc_net import list_processes, list_services
from desktop.route_analyzer import analyze_process, analyze_service, analyze_token
from desktop.route_tokens import canonical_route_token, token_payload
from desktop.services.connection import ConnectionService
from desktop.services.elevation import ElevationService
from desktop.services.settings import SettingsService
from desktop.services.status import C_DANGER, C_MUTED, present_status
from desktop.ui.messages import format_user_error
from desktop.ui.settings_map import (
    apply_mode_to_settings,
    cfg_to_settings,
    settings_defaults,
)
from desktop.ui.theme import (
    THEME_DARK,
    THEME_ERGOMS,
    apply_theme_map,
    load_ui_theme,
    save_ui_theme,
    status_color,
    status_role,
)
from desktop.update import (
    CheckResult,
    ReleaseInfo,
    apply_downloaded,
    can_apply_in_place,
    download_asset,
    fetch_latest,
    open_release_page,
)

LogFn = Callable[[str], None]

_C_MUTED = C_MUTED
_C_DANGER = C_DANGER


def _analyze_spec(spec: str) -> dict[str, Any]:
    raw = (spec or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        kind = str(data.get("kind") or "").lower()
        if kind == "service":
            return analyze_service(str(data.get("name") or data.get("display") or ""))
        return analyze_process(
            pid=int(data.get("pid") or 0),
            name=str(data.get("name") or ""),
            path=str(data.get("path") or ""),
            display=str(data.get("display") or data.get("name") or ""),
        )
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


def _release_from_dict(raw: object) -> ReleaseInfo | None:
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip()
    url = str(raw.get("asset_url") or "").strip()
    if not version or not url:
        return None
    return ReleaseInfo(
        version=version,
        tag=str(raw.get("tag") or f"v{version}"),
        html_url=str(raw.get("html_url") or ""),
        asset_name=str(raw.get("asset_name") or ""),
        asset_url=url,
    )


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
    updateAvailableChanged = Signal()
    updateVersionChanged = Signal()
    updateCheckingChanged = Signal()
    uiThemeChanged = Signal()

    _bgFinished = Signal(str)
    _updateCheckFinished = Signal(str, bool)
    _updateProgress = Signal(str)
    _updateApplyReady = Signal()
    _updateApplyFailed = Signal(str)
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
        self._ui_theme = load_ui_theme()
        self._status_role = "muted"
        self._status_color = status_color(self._ui_theme, _C_MUTED)
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
        self._busy_action: Action | None = None
        self._status_queued = False
        self._status_force = False
        self._update_available = False
        self._update_version = ""
        self._update_info: ReleaseInfo | None = None
        self._update_checking = False
        self._update_applying = False
        self._update_toast_shown = False
        self._update_retry_on_socks = False
        self._update_socks_seen = False

        self._suspend_autosave = True
        self._settings = QQmlPropertyMap(self)
        for key, value in settings_defaults().items():
            self._settings.insert(key, value)
        self._theme = QQmlPropertyMap(self)
        apply_theme_map(self._theme, self._ui_theme)

        self._bgFinished.connect(self._on_bg_finished)
        self._statusReady.connect(self._on_status_ready)
        self._statusFailed.connect(self._apply_status_error)
        self._updateCheckFinished.connect(self._on_update_check_finished)
        self._updateProgress.connect(self._on_update_progress)
        self._updateApplyReady.connect(self._on_update_apply_ready)
        self._updateApplyFailed.connect(self._on_update_apply_failed)

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

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(350)
        self._autosave_timer.timeout.connect(self._autosave_settings)
        self._settings.valueChanged.connect(self._on_setting_changed)
        self._suspend_autosave = False

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._on_poll_tick)
        self._schedulePoll.connect(self._status_timer.start)
        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.timeout.connect(self._busy_timed_out)
        QTimer.singleShot(300, self._on_poll_tick)
        QTimer.singleShot(2000, self._startup_update_check)
        if autostart.launched_from_autostart():
            self._start_hidden = True
        resume_action = action_from_resume(env(ENV_RESUME).lower())
        if resume_action is Action.CONNECT:
            QTimer.singleShot(400, self.enableConnection)
        elif resume_action is Action.DISCONNECT:
            QTimer.singleShot(400, self.disableConnection)
        elif resume_action is Action.TUN_ON:
            QTimer.singleShot(400, self.enableTun)
        elif resume_action is Action.TUN_OFF:
            QTimer.singleShot(400, self.disableTun)
        elif autostart.launched_from_autostart() and self._config_ready:
            QTimer.singleShot(600, self.enableConnection)
        try:
            if not self.client._vpn_process_up():
                self.client.teardown_overrides_if_dirty()
                self._overrides_cleared = True
        except Exception:  # noqa: BLE001
            pass

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

    @Property(QObject, constant=True)
    def themeTokens(self) -> QQmlPropertyMap:
        return self._theme

    @Property(str, notify=uiThemeChanged)
    def uiTheme(self) -> str:
        return self._ui_theme

    @Property(str, constant=True)
    def appVersion(self) -> str:
        return __version__

    @Property(bool, notify=updateAvailableChanged)
    def updateAvailable(self) -> bool:
        return self._update_available

    @Property(str, notify=updateVersionChanged)
    def updateVersion(self) -> str:
        return self._update_version

    @Property(bool, notify=updateCheckingChanged)
    def updateChecking(self) -> bool:
        return self._update_checking

    # ── slots ───────────────────────────────────────────────────────────

    @Slot(str)
    def setUiTheme(self, name: str) -> None:
        next_id = save_ui_theme(name)
        if next_id == self._ui_theme:
            return
        self._ui_theme = next_id
        apply_theme_map(self._theme, next_id)
        self._status_color = status_color(next_id, self._status_role)
        self.uiThemeChanged.emit()
        self.statusColorChanged.emit()

    @Slot()
    def toggleUiTheme(self) -> None:
        self.setUiTheme(THEME_ERGOMS if self._ui_theme != THEME_ERGOMS else THEME_DARK)

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
    def normalizeRouteToken(self, raw: str) -> str:
        return canonical_route_token(raw)

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

    def _saved_message(self, text: str) -> str:
        if self._active:
            return f"{text}. Применится при следующем подключении."
        return text

    def _on_setting_changed(self, _key: str, _value: object) -> None:
        if self._suspend_autosave or self._closing:
            return
        self._autosave_timer.start()

    @Slot()
    def _autosave_settings(self) -> None:
        if self._closing or self._suspend_autosave:
            return
        try:
            self._write_settings_to_disk()
            self._sync_config_ready()
        except Exception as exc:  # noqa: BLE001
            self._toast_err(exc)

    @Slot()
    def saveExceptions(self) -> None:
        try:
            self._write_settings_to_disk()
            self._enqueue_log("Правила сохранены")
        except Exception as exc:  # noqa: BLE001
            self._toast_err(exc)

    def _handoff_if_needed(self, action: Action | str) -> bool:
        """Relaunch elevated once. True = caller must stop (handoff or cancel)."""
        key = action.elevation_key if isinstance(action, Action) else action
        if not self.elevation.needed(key):
            return False
        flags = [action.cli_flag] if isinstance(action, Action) else [f"--{action}"]
        if not isinstance(action, Action):
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
        if os.name == "nt":
            msg = "Нужны права администратора один раз — потом окна Windows больше не появятся."
        else:
            msg = "Нужен пароль sudo (TUN / kill switch)."
        self.toast.emit(msg, "error")
        return True

    @Slot()
    def toggleConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready and not self._active and not self._kill_switch_on:
            self.toast.emit("Загрузите конфиг", "warn")
            return
        if self._active or self._kill_switch_on:
            if self._handoff_if_needed(Action.DISCONNECT):
                return
            self._run_bg(self.connection.disable, Action.DISCONNECT)
        else:
            if self._handoff_if_needed(Action.CONNECT):
                return
            self._run_bg(self.connection.enable, Action.CONNECT)

    @Slot()
    def activatePower(self) -> None:
        if self._can_reconnect:
            self.reconnectConnection()
            return
        self.toggleConnection()

    @Slot()
    def enableConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready:
            self.toast.emit("Загрузите конфиг", "warn")
            return
        if self._handoff_if_needed(Action.CONNECT):
            return
        self._run_bg(self.connection.enable, Action.CONNECT)

    @Slot()
    def disableConnection(self) -> None:
        if self._busy:
            return
        if self._handoff_if_needed(Action.DISCONNECT):
            return
        self._run_bg(self.connection.disable, Action.DISCONNECT)

    @Slot()
    def reconnectConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready and not self._active and not self._kill_switch_on:
            self.toast.emit("Загрузите конфиг", "warn")
            return
        if self._handoff_if_needed(Action.RECONNECT):
            return
        self._run_bg(self.connection.reconnect, Action.RECONNECT)

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
        if self._handoff_if_needed(Action.TUN_ON):
            return
        self._run_bg(self.connection.enable_tun, Action.TUN_ON)

    @Slot()
    def disableTun(self) -> None:
        if self._busy:
            return
        if self._handoff_if_needed(Action.TUN_OFF):
            return
        self._run_bg(self.connection.disable_tun, Action.TUN_OFF)

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
        if self._busy:
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
            self._toast_err(exc)

    def _apply_cfg_to_settings(self, cfg: dict[str, Any]) -> None:
        prev = self._suspend_autosave
        self._suspend_autosave = True
        try:
            self._set_corporate(infer_corporate(cfg))
            self._mode_label = "Корпоративный" if self._corporate else "VPN"
            for key, value in cfg_to_settings(cfg).items():
                self._settings.insert(key, value)
            loaded = bool(self._settings.value("awgLoaded"))
            source = self.settings_svc.awg_source_name()
            if loaded:
                self._settings.insert("awgSummary", source or "amneziawg.conf")
            self._sync_config_ready()
        finally:
            self._suspend_autosave = prev

    def _write_settings_to_disk(self) -> None:
        cfg = self.settings_svc.write_from_map(
            self._settings.value, corporate=bool(self._corporate)
        )
        self._apply_cfg_to_settings(cfg)

    @Slot(bool)
    def applyCorporateMode(self, on: bool) -> None:
        if self._busy:
            return
        prev = self._suspend_autosave
        self._suspend_autosave = True
        try:
            self._set_corporate(on)
            apply_mode_to_settings(
                self._settings.value, self._settings.insert, corporate=on
            )
            self._mode_label = "Корпоративный" if on else "VPN"
            self._write_settings_to_disk()
            self._enqueue_log(
                "режим: корпоративный" if on else "режим: обычный VPN"
            )
            if self._active:
                self.toast.emit(self._saved_message("Сохранено"), "info")
        except Exception as exc:  # noqa: BLE001
            self._toast_err(exc)
        finally:
            self._suspend_autosave = prev

    @Slot()
    def importConfigFile(self) -> None:
        if self._busy:
            self.toast.emit("Дождитесь окончания операции", "warn")
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
            self._toast_err(exc)

    def _import_config_path(self, src: Path) -> None:
        from desktop.config_crypto import MAGIC

        raw = src.read_bytes()
        password = None
        encrypted = raw.startswith(MAGIC) or src.suffix.lower() == ".enc"
        if encrypted:
            password, ok = QInputDialog.getText(
                None,
                "ERGOMS SECURE CONNECTION",
                "Пароль к файлу:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not password:
                return
        result = self.settings_svc.import_path(src, password=password)
        if result.kind == "awg":
            self.loadSettings()
            self._enqueue_log("AmneziaWG .conf загружен")
            self.toast.emit(self._saved_message("AmneziaWG .conf загружен"), "info")
            self._refresh_status(force=True)
            return
        self.loadSettings()
        self._enqueue_log("Конфиг загружен")
        self.toast.emit(self._saved_message("Конфиг загружен" + result.extra), "info")
        self._refresh_status(force=True)

    @Slot()
    def importAwgConfFile(self) -> None:
        if self._busy:
            self.toast.emit("Дождитесь окончания операции", "warn")
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
            self._toast_err(exc)

    def _import_awg_conf_text(self, text: str, *, source_name: str = "") -> None:
        self.settings_svc.import_awg_text(text, source_name=source_name)
        self.loadSettings()
        self._enqueue_log("AmneziaWG .conf загружен")
        self.toast.emit(self._saved_message("AmneziaWG .conf загружен"), "info")
        self._refresh_status(force=True)

    @Slot()
    def exportConfigFile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Сохранить config.json",
            "config.json",
            "JSON (*.json);;All files (*)",
        )
        if not path:
            return
        dest = Path(path)
        try:
            self.settings_svc.export_to(dest)
            self._enqueue_log(f"Копия конфига → {dest}")
            self.toast.emit("JSON сохранён. AmneziaWG — отдельный .conf", "info")
        except Exception as exc:  # noqa: BLE001
            self._toast_err(exc)

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
        if self._busy:
            self.toast.emit("Дождитесь окончания операции", "warn")
            return
        self._autosave_settings()

    @Slot()
    def copyLog(self) -> None:
        from desktop.ui.clipboard import copy_text

        text = "\n".join(self._log_lines)
        if copy_text(text):
            self.toast.emit("Журнал скопирован", "info")
            return
        self.toast.emit("Буфер обмена занят — повторите копирование", "error")

    @Slot()
    def checkForUpdate(self) -> None:
        self._check_for_update(manual=True)

    @Slot()
    def _startup_update_check(self) -> None:
        self._check_for_update(manual=False)

    def _check_for_update(self, *, manual: bool) -> None:
        if self._update_checking or self._update_applying:
            return
        self._set_update_checking(True)
        self._enqueue_log("проверка обновлений…")
        socks_port = int(self._socks_port or 1080)

        def work() -> None:
            result = fetch_latest(socks_port=socks_port)
            self._updateCheckFinished.emit(json.dumps(result.as_dict(), ensure_ascii=False), manual)

        self._spawn(work)

    def _set_update_checking(self, on: bool) -> None:
        if on == self._update_checking:
            return
        self._update_checking = on
        self.updateCheckingChanged.emit()

    @Slot(str, bool)
    def _on_update_check_finished(self, payload: str, manual: bool) -> None:
        self._set_update_checking(False)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"error": "не удалось разобрать ответ", "release": None}
        result = CheckResult(
            release=_release_from_dict(data.get("release")),
            current_is_latest=bool(data.get("current_is_latest")),
            error=str(data.get("error") or ""),
        )
        if result.error:
            self._update_retry_on_socks = not self._socks_up
            self._enqueue_log(f"проверка обновлений: {result.error}")
            if manual:
                self._toast_err(result.error[:120])
            return
        self._update_retry_on_socks = False
        info = result.release
        if info is None:
            self._set_update_info(None)
            if manual:
                self.toast.emit("Уже последняя версия", "info")
            return
        self._set_update_info(info)
        self._enqueue_log(f"доступна версия {info.version}")
        if not self._update_toast_shown:
            self._update_toast_shown = True
            self.toast.emit(f"Доступна версия {info.version}", "info")

    def _set_update_info(self, info: ReleaseInfo | None) -> None:
        available = info is not None
        version = info.version if info is not None else ""
        self._update_info = info
        if available != self._update_available:
            self._update_available = available
            self.updateAvailableChanged.emit()
        if version != self._update_version:
            self._update_version = version
            self.updateVersionChanged.emit()

    @Slot()
    def startUpdate(self) -> None:
        if self._busy or self._update_applying or self._update_checking:
            return
        info = self._update_info
        if info is None:
            self._check_for_update(manual=True)
            return
        if not can_apply_in_place():
            open_release_page(info.html_url)
            return
        self._update_applying = True
        self._set_busy(True, "Скачиваю обновление…", timeout_ms=600_000)
        socks_port = int(self._socks_port or 1080)

        def work() -> None:
            try:
                dest_dir = self.paths.var_dir / "updates"
                dest_dir.mkdir(parents=True, exist_ok=True)
                path = download_asset(info, dest_dir, socks_port=socks_port)
                self._updateProgress.emit("Отключаю VPN…")
                try:
                    self.connection.disable()
                except Exception as exc:  # noqa: BLE001
                    self._enqueue_log(f"отключение перед обновлением: {exc}")
                self._updateProgress.emit("Запускаю установщик…")
                apply_downloaded(path, pid=os.getpid(), relaunch=gui_command())
                self._updateApplyReady.emit()
            except Exception as exc:  # noqa: BLE001
                self._updateApplyFailed.emit(str(exc))

        self._spawn(work)

    @Slot(str)
    def _on_update_progress(self, text: str) -> None:
        if not self._busy:
            return
        self._busy_text = text
        self.busyTextChanged.emit()

    @Slot()
    def _on_update_apply_ready(self) -> None:
        self._update_applying = False
        self.quitApp()

    @Slot(str)
    def _on_update_apply_failed(self, err: str) -> None:
        self._update_applying = False
        self._set_busy(False)
        self._enqueue_log(f"обновление: {err}")
        self._toast_err(err[:180] or "Не удалось обновить")

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

    def _toast_err(self, err: object) -> None:
        self.toast.emit(format_user_error(err), "error")

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

    def _set_busy(self, busy: bool, waiting: str = "Подождите…", *, timeout_ms: int = 180_000) -> None:
        self._busy = busy
        self._busy_text = waiting if busy else ""
        self.busyChanged.emit()
        self.busyTextChanged.emit()
        if busy:
            self._busy_timer.start(timeout_ms)
        else:
            self._busy_timer.stop()

    @Slot()
    def _busy_timed_out(self) -> None:
        if not self._busy:
            return
        self._update_applying = False
        self._enqueue_log("операция слишком долгая — снимаю блокировку кнопок")
        self._finish_await_status()
        self._refresh_status(force=True)

    def _run_bg(self, fn: Callable[[], None], action: Action) -> None:
        if self._busy:
            return
        self._busy_action = action
        self._busy_intent = action.busy_intent
        self._await_status = True
        self._set_busy(True, action.waiting_text)

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
        action = self._busy_action
        if err:
            self._await_status = False
            self._busy_intent = ""
            self._busy_action = None
            self._set_busy(False)
            self._toast_err(err)
            self._refresh_status(force=True)
            return
        self._refresh_status(force=True)
        if action in (Action.CONNECT, Action.RECONNECT, Action.TUN_ON):
            from desktop.leak_shield import consume_browser_toast

            try:
                if consume_browser_toast(self.paths.var_dir):
                    self.toast.emit(
                        "Перезапустите браузер, чтобы трафик шёл через VPN",
                        "info",
                    )
            except Exception:  # noqa: BLE001
                pass

    def _finish_await_status(self) -> None:
        if not self._await_status and not self._busy:
            return
        self._await_status = False
        self._busy_intent = ""
        self._busy_action = None
        self._set_busy(False)

    @Slot()
    def _on_poll_tick(self) -> None:
        self._refresh_status(force=False)

    def _refresh_status(self, force: bool = False) -> None:
        if self._closing:
            return
        if self._status_busy:
            self._status_queued = True
            self._status_force = self._status_force or force
            return
        self._status_busy = True
        want_force = force or self._status_force
        self._status_force = False

        def work() -> None:
            try:
                st = self.client.status(include_git=False)
                err = ""
            except Exception as exc:  # noqa: BLE001
                st = None
                err = str(exc)
            if self._closing:
                return
            if err:
                self._statusFailed.emit(err)
            elif st is not None:
                self._statusReady.emit(st, want_force)
            QMetaObject.invokeMethod(self, "_status_work_done", Qt.QueuedConnection)

        self._spawn(work)

    @Slot()
    def _status_work_done(self) -> None:
        queued = self._status_queued
        force = self._status_force
        self._status_busy = False
        self._status_queued = False
        self._status_force = False
        if self._closing:
            return
        if queued:
            self._refresh_status(force=force)
            return
        delay = 5000 if self._page == "home" and not self._busy else 10000
        self._schedulePoll.emit(delay)

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
        self._status_role = status_role(_C_DANGER)
        self._status_color = status_color(self._ui_theme, _C_DANGER)
        self._can_reconnect = bool(self._active or self._kill_switch_on)
        self.statusTitleChanged.emit()
        self.statusSubChanged.emit()
        self.statusColorChanged.emit()
        self.canReconnectChanged.emit()
        self._finish_await_status()

    def _apply_status(self, st: dict[str, Any], *, force: bool = False) -> None:
        view = present_status(st, config_ready=self._config_ready, corporate=self._corporate)
        singbox = view.singbox_up
        tun = view.tun
        connecting = bool(st.get("connecting")) or view.title == "Подключение…"
        tun_wanted = bool(st.get("tun_wanted", tun))
        tun_ready = bool(st.get("tun_ready")) if "tun_ready" in st else (not tun_wanted or tun)
        probe_err = str(st.get("exit_probe_error") or "")
        if not force and (
            view.signature == self._last_status_sig
            or (self._busy and not self._await_status)
        ):
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
        self._last_status_sig = view.signature
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
        if (
            self._socks_up
            and not self._update_socks_seen
            and self._update_retry_on_socks
            and not self._update_checking
            and not self._update_available
        ):
            self._update_retry_on_socks = False
            QTimer.singleShot(800, self._startup_update_check)
        self._update_socks_seen = self._socks_up
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
        self._status_role = status_role(view.color)
        self._status_color = status_color(self._ui_theme, view.color)
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
