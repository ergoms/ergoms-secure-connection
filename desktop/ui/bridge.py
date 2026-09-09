"""QObject bridge: QML ↔ OpsClient (background workers, status, settings)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit

from desktop import __version__
from desktop import autostart
from desktop.client import OpsClient
from desktop.config_crypto import MAGIC, decrypt_config
from desktop.config_io import (
    CORPORATE_BYPASS_PRESET,
    CORPORATE_PROXY_PRESET,
    STANDARD_BYPASS_PRESET,
    apply_config,
    apply_corporate_profile,
    apply_standard_profile,
    default_config_template,
    ensure_config_defaults,
    infer_corporate,
    load_config,
    save_config,
)
from desktop.paths import Paths

LogFn = Callable[[str], None]

_C_MUTED = "#9aa3b5"
_C_ACCENT = "#2dd4a8"
_C_OK = "#2dd4a8"
_C_WARN = "#e6c07b"
_C_DANGER = "#f07178"


def _scope_label(scope: str) -> str:
    return {"full": "Всё", "github": "GitHub"}.get(scope, scope or "—")


class GuiBridge(QObject):
    """In-process façade for the QML shell. Does not change OpsClient."""

    toast = Signal(str, str)  # message, kind: info|error|warn
    logAppended = Signal(str)
    showRequested = Signal()
    hideRequested = Signal()
    quitRequested = Signal()

    activeChanged = Signal()
    tunChanged = Signal()
    killSwitchOnChanged = Signal()
    busyChanged = Signal()
    busyTextChanged = Signal()
    statusTitleChanged = Signal()
    statusSubChanged = Signal()
    statusColorChanged = Signal()
    serverTargetChanged = Signal()
    scopeChanged = Signal()
    modeLabelChanged = Signal()
    logTextChanged = Signal()
    pageChanged = Signal()
    socksPortChanged = Signal()
    httpPortChanged = Signal()
    pacPortChanged = Signal()
    socksUpChanged = Signal()
    httpUpChanged = Signal()
    pacUpChanged = Signal()
    watchdogUpChanged = Signal()
    reverseSshUpChanged = Signal()
    reverseSshPortChanged = Signal()
    singboxUpChanged = Signal()
    powerTextChanged = Signal()
    tunButtonTextChanged = Signal()
    configReadyChanged = Signal()
    corporateChanged = Signal()
    autostartChanged = Signal()

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
        self.client = OpsClient(paths=self.paths, log=self._enqueue_log)

        self._active = False
        self._tun = False
        self._kill_switch_on = False
        self._busy = False
        self._busy_text = ""
        self._status_title = "Отключено"
        self._status_sub = "Нажмите «Подключить»"
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
        self._tun_button_text = "TUN вкл"
        self._config_ready = False
        self._corporate = False
        self._autostart = autostart.is_enabled()
        self._start_hidden = False
        self._last_status_sig = ""
        self._status_busy = False
        self._closing = False
        self._overrides_cleared = False

        self._settings = QQmlPropertyMap(self)
        for key, value in _settings_defaults().items():
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
        if autostart.launched_from_autostart():
            self._start_hidden = True
            if self._config_ready:
                QTimer.singleShot(600, self.enableConnection)

    # ── properties ──────────────────────────────────────────────────────

    @Property(bool, notify=activeChanged)
    def active(self) -> bool:
        return self._active

    @Property(bool, notify=tunChanged)
    def tun(self) -> bool:
        return self._tun

    @Property(bool, notify=killSwitchOnChanged)
    def killSwitchOn(self) -> bool:
        return self._kill_switch_on

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

    @Property(str, notify=serverTargetChanged)
    def serverTarget(self) -> str:
        return self._server_target

    @Property(str, notify=scopeChanged)
    def scope(self) -> str:
        return self._scope

    @Property(str, notify=modeLabelChanged)
    def modeLabel(self) -> str:
        return self._mode_label

    @Property(str, notify=logTextChanged)
    def logText(self) -> str:
        return "\n".join(self._log_lines)

    @Property(str, notify=pageChanged)
    def page(self) -> str:
        return self._page

    @Property(int, notify=socksPortChanged)
    def socksPort(self) -> int:
        return self._socks_port

    @Property(int, notify=httpPortChanged)
    def httpPort(self) -> int:
        return self._http_port

    @Property(int, notify=pacPortChanged)
    def pacPort(self) -> int:
        return self._pac_port

    @Property(bool, notify=socksUpChanged)
    def socksUp(self) -> bool:
        return self._socks_up

    @Property(bool, notify=httpUpChanged)
    def httpUp(self) -> bool:
        return self._http_up

    @Property(bool, notify=pacUpChanged)
    def pacUp(self) -> bool:
        return self._pac_up

    @Property(bool, notify=watchdogUpChanged)
    def watchdogUp(self) -> bool:
        return self._watchdog_up

    @Property(bool, notify=reverseSshUpChanged)
    def reverseSshUp(self) -> bool:
        return self._reverse_ssh_up

    @Property(int, notify=reverseSshPortChanged)
    def reverseSshPort(self) -> int:
        return self._reverse_ssh_port

    @Property(bool, notify=singboxUpChanged)
    def singboxUp(self) -> bool:
        return self._singbox_up

    @Property(str, notify=powerTextChanged)
    def powerText(self) -> str:
        return self._power_text

    @Property(str, notify=tunButtonTextChanged)
    def tunButtonText(self) -> str:
        return self._tun_button_text

    @Property(bool, notify=configReadyChanged)
    def configReady(self) -> bool:
        return self._config_ready

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
    def version(self) -> str:
        return __version__

    @Property(str, constant=True)
    def dataRoot(self) -> str:
        return str(self.paths.root)

    @Property(QObject, constant=True)
    def settings(self) -> QQmlPropertyMap:
        return self._settings

    # ── slots ───────────────────────────────────────────────────────────

    @Slot(str)
    def setPage(self, page: str) -> None:
        if page not in ("home", "settings", "log") or page == self._page:
            return
        self._page = page
        self.pageChanged.emit()

    @Slot()
    def toggleConnection(self) -> None:
        if self._busy:
            return
        if not self._config_ready and not self._active:
            self.importConfigFile()
            return
        if self._active or self._kill_switch_on:
            self._run_bg(self.client.disable, waiting="Отключение…")
        else:
            self._run_bg(self.client.enable, waiting="Подключение…")

    @Slot()
    def enableConnection(self) -> None:
        if not self._busy:
            self._run_bg(self.client.enable, waiting="Подключение…")

    @Slot()
    def disableConnection(self) -> None:
        if not self._busy:
            self._run_bg(self.client.disable, waiting="Отключение…")

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
        if not self._busy:
            self._run_bg(self.client.enable_tun, waiting="Включаю TUN…")

    @Slot()
    def disableTun(self) -> None:
        if not self._busy:
            self._run_bg(self.client.disable_tun, waiting="Выключаю TUN…")

    @Slot()
    def loadSettings(self) -> None:
        if not self.paths.config_path.is_file():
            self._sync_config_ready()
            return
        cfg = load_config(self.paths.config_path)
        server = cfg.get("server") or {}
        tun = cfg.get("tun") or {}
        tr = cfg.get("transport") or {}
        bypass = cfg.get("proxy_bypass") or []
        self._set_corporate(infer_corporate(cfg))
        self._mode_label = "Корпоративный" if self._corporate else "VPN"
        self.modeLabelChanged.emit()
        self._settings.insert("socksScope", str(cfg.get("socks_scope") or "full"))
        self._settings.insert("tunAuto", bool(tun.get("enabled")))
        self._settings.insert("killSwitch", bool(cfg.get("kill_switch", True)))
        self._settings.insert("httpBridgePort", str(cfg.get("http_bridge_port") or 1088))
        self._settings.insert(
            "useProxy",
            bool(cfg.get("use_proxy")) or infer_corporate(cfg),
        )
        self._settings.insert("corporateProxy", str(cfg.get("corporate_proxy") or ""))
        self._settings.insert("serverHost", str(server.get("host") or ""))
        self._settings.insert("serverPort", str(server.get("port") or 443))
        self._settings.insert("serverSocks", str(server.get("local_socks_port") or 1080))
        self._settings.insert("proxyBypass", ", ".join(str(x) for x in bypass))
        self._settings.insert(
            "proxyBypassVia", str(cfg.get("proxy_bypass_via") or "direct")
        )
        self._settings.insert("trUuid", str(tr.get("uuid") or ""))
        self._settings.insert("trPublicKey", str(tr.get("public_key") or ""))
        self._settings.insert("trShortId", str(tr.get("short_id") or ""))
        self._settings.insert(
            "trServerName", str(tr.get("server_name") or "www.cloudflare.com")
        )
        self._settings.insert("trPort", str(tr.get("port") or 443))
        rev = cfg.get("reverse_ssh") or {}
        self._settings.insert("reverseSsh", bool(rev.get("enabled")))
        self._settings.insert("reverseSshListen", str(rev.get("listen_port") or 2222))
        self._settings.insert("reverseSshVpsUser", str(rev.get("vps_user") or "root"))
        self._settings.insert("reverseSshVpsPort", str(rev.get("vps_port") or 22))
        self._sync_config_ready()

    def _set_corporate(self, on: bool) -> None:
        self._settings.insert("corporate", on)
        if on != self._corporate:
            self._corporate = on
            self.corporateChanged.emit()

    @Slot(bool)
    def setAutostart(self, on: bool) -> None:
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

    @Slot(bool)
    def applyCorporateMode(self, on: bool) -> None:
        self._set_corporate(on)
        if on:
            self._settings.insert("socksScope", "github")
            self._settings.insert("useProxy", True)
            self._settings.insert("corporateProxy", CORPORATE_PROXY_PRESET)
            self._settings.insert("proxyBypass", ", ".join(CORPORATE_BYPASS_PRESET))
            self._settings.insert("proxyBypassVia", "direct")
        else:
            self._settings.insert("socksScope", "full")
            self._settings.insert("useProxy", False)
            self._settings.insert("corporateProxy", "")
            self._settings.insert("proxyBypass", ", ".join(STANDARD_BYPASS_PRESET))
            self._settings.insert("proxyBypassVia", "direct")
            self._settings.insert("reverseSsh", False)
        self._mode_label = "Корпоративный" if on else "VPN"
        self.modeLabelChanged.emit()

    @Slot()
    def importConfigFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Конфиг ERGOMS SECURE CONNECTION",
            "",
            "Config (*.json *.enc);;JSON (*.json);;Encrypted (*.enc);;All files (*)",
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
        if encrypted:
            password, ok = QInputDialog.getText(
                None,
                "ERGOMS SECURE CONNECTION",
                "Пароль к файлу:",
                QLineEdit.EchoMode.Password,
            )
            if not ok or not password:
                return
            cfg = decrypt_config(raw, password)
        else:
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("Файл не JSON-объект")
            cfg = data
        cfg = ensure_config_defaults(cfg)
        self.paths.ensure_dirs()
        save_config(self.paths.config_path, cfg)
        apply_config(self.paths.config_path, force=True)
        self.loadSettings()
        self._enqueue_log(f"Конфиг загружен из {src}")
        self.toast.emit("Конфиг загружен", "info")
        self._refresh_status(force=True)

    def _sync_config_ready(self) -> None:
        uuid = str(self._settings.value("trUuid") or "").strip()
        host = str(self._settings.value("serverHost") or "").strip()
        ready = (
            bool(uuid)
            and "REPLACE" not in uuid.upper()
            and len(uuid) >= 8
            and bool(host)
            and "YOUR_VPS" not in host
        )
        if ready != self._config_ready:
            self._config_ready = ready
            self.configReadyChanged.emit()
        if not ready and not self._active and not self._busy:
            self._status_title = "Нет конфига"
            self._status_sub = "Загрузите config.json или .enc"
            self._power_text = "Загрузить конфиг"
            self.statusTitleChanged.emit()
            self.statusSubChanged.emit()
            self.powerTextChanged.emit()

    @Slot()
    def saveSettings(self) -> None:
        try:
            if self.paths.config_path.is_file():
                cfg = load_config(self.paths.config_path)
            else:
                cfg = default_config_template()
            s = self._settings
            corporate = bool(self._corporate)
            cfg["corporate"] = corporate
            cfg["http_bridge_port"] = int(
                str(s.value("httpBridgePort") or "1088").strip() or "1088"
            )
            if corporate:
                apply_corporate_profile(cfg)
                cfg["socks_scope"] = (
                    str(s.value("socksScope") or "github").strip() or "github"
                )
                cfg["use_proxy"] = True
                cfg["corporate_proxy"] = str(s.value("corporateProxy") or "").strip()
            else:
                apply_standard_profile(cfg)
                cfg["use_proxy"] = bool(s.value("useProxy"))
                cfg["corporate_proxy"] = (
                    str(s.value("corporateProxy") or "").strip()
                    if cfg["use_proxy"]
                    else ""
                )
            cfg.pop("ssh", None)
            cfg["server"] = {
                "host": str(s.value("serverHost") or "").strip(),
                "port": int(str(s.value("serverPort") or "443").strip() or "443"),
                "local_socks_port": int(
                    str(s.value("serverSocks") or "1080").strip() or "1080"
                ),
            }
            cfg.pop("worker_base_url", None)
            if corporate:
                raw_bypass = str(s.value("proxyBypass") or "").strip()
                cfg["proxy_bypass"] = [
                    x.strip() for x in raw_bypass.split(",") if x.strip()
                ]
                cfg["proxy_bypass_via"] = "direct"
            cfg["kill_switch"] = bool(s.value("killSwitch"))
            cfg.setdefault("tun", {})
            cfg["tun"]["enabled"] = bool(s.value("tunAuto")) or cfg["kill_switch"]
            cfg["tun"]["elevate"] = True
            cfg["tun"]["sing_box_path"] = ""
            cfg.setdefault("transport", {})
            cfg["transport"]["type"] = "vless-reality"
            cfg["transport"]["uuid"] = str(s.value("trUuid") or "").strip()
            cfg["transport"]["public_key"] = str(s.value("trPublicKey") or "").strip()
            cfg["transport"]["short_id"] = str(s.value("trShortId") or "").strip()
            cfg["transport"]["server_name"] = (
                str(s.value("trServerName") or "").strip() or "www.cloudflare.com"
            )
            cfg["transport"]["port"] = int(
                str(s.value("serverPort") or s.value("trPort") or "443").strip() or "443"
            )
            cfg.setdefault("reverse_ssh", {})
            if corporate:
                cfg["reverse_ssh"]["enabled"] = bool(s.value("reverseSsh"))
                cfg["reverse_ssh"]["listen_port"] = int(
                    str(s.value("reverseSshListen") or "2222").strip() or "2222"
                )
                cfg["reverse_ssh"]["vps_user"] = (
                    str(s.value("reverseSshVpsUser") or "").strip() or "root"
                )
                cfg["reverse_ssh"]["vps_port"] = int(
                    str(s.value("reverseSshVpsPort") or "22").strip() or "22"
                )
            else:
                cfg["reverse_ssh"]["enabled"] = False
            save_config(self.paths.config_path, cfg)
            apply_config(self.paths.config_path, force=True)
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
    def hideWindow(self) -> None:
        self.hideRequested.emit()

    @Slot()
    def showWindow(self) -> None:
        self.showRequested.emit()

    @Slot()
    def teardownNow(self) -> None:
        """Synchronous undo of git/PAC/Docker leftovers (quit / aboutToQuit)."""
        try:
            self.client.teardown_overrides()
        except Exception:  # noqa: BLE001
            pass

    @Slot()
    def quitApp(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._status_timer.stop()
        self.teardownNow()

        def work() -> None:
            try:
                self.client.disable()
            except Exception:  # noqa: BLE001
                try:
                    self.client.stop_http_bridge()
                except Exception:  # noqa: BLE001
                    pass
                self.teardownNow()
            self.quitRequested.emit()

        threading.Thread(target=work, daemon=True).start()

    # ── internals ───────────────────────────────────────────────────────

    def _enqueue_log(self, msg: str) -> None:
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
        if busy:
            self._status_sub = waiting
            self.statusSubChanged.emit()

    def _run_bg(self, fn: Callable[[], None], waiting: str = "Подождите…") -> None:
        if self._busy:
            return
        self._set_busy(True, waiting)

        def work() -> None:
            err = ""
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                self._enqueue_log(f"ошибка: {exc}")
            self._bgFinished.emit(err)

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def _on_bg_finished(self, err: str) -> None:
        self._set_busy(False)
        if err:
            self.toast.emit(err, "error")
        self._refresh_status(force=True)

    @Slot()
    def _on_poll_tick(self) -> None:
        self._refresh_status(force=False)

    def _refresh_status(self, force: bool = False) -> None:
        if self._closing:
            return
        if self._status_busy:
            self._status_timer.start(1000)
            return

        def work() -> None:
            self._status_busy = True
            try:
                st = self.client.status(include_git=False)
                err = ""
            except Exception as exc:  # noqa: BLE001
                st = None
                err = str(exc)
            self._status_busy = False
            if self._closing:
                return
            if err:
                self._statusFailed.emit(err)
            elif st is not None:
                self._statusReady.emit(st, force)
            delay = 5000 if self._page == "home" and not self._busy else 10000
            if not self._closing:
                self._schedulePoll.emit(delay)

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, bool)
    def _on_status_ready(self, st: object, force: bool) -> None:
        if isinstance(st, dict):
            self._apply_status(st, force=force)

    @Slot(str)
    def _apply_status_error(self, err: str) -> None:
        if self._busy:
            return
        self._status_title = "Ошибка"
        self._status_sub = err[:80]
        self._status_color = _C_DANGER
        self.statusTitleChanged.emit()
        self.statusSubChanged.emit()
        self.statusColorChanged.emit()

    def _apply_status(self, st: dict[str, Any], *, force: bool = False) -> None:
        singbox = bool(st.get("singbox_running"))
        tun = bool(st.get("tun_running"))
        socks_up = bool(st.get("socks_up")) if "socks_up" in st else singbox
        http_up = bool(st.get("http_up")) if "http_up" in st else singbox
        pac_up = bool(st.get("pac_up")) if "pac_up" in st else singbox
        active = bool(singbox or tun)
        scope = _scope_label(str(st.get("socks_scope") or ""))
        target = str(st.get("server_target") or st.get("ssh_target") or "—")
        ks_on = bool(st.get("kill_switch_applied") or (active and st.get("kill_switch")))
        sig = (
            f"{singbox}|{tun}|{active}|{socks_up}|{http_up}|{pac_up}|{scope}|{target}"
            f"|{st.get('watchdog_running')}|{st.get('reverse_ssh_running')}"
            f"|{st.get('reverse_ssh_listen')}|{ks_on}"
        )
        if not force and (sig == self._last_status_sig or self._busy):
            return
        self._last_status_sig = sig

        self._active = active
        self._tun = tun
        self._kill_switch_on = ks_on
        self._singbox_up = singbox
        if active:
            self._overrides_cleared = False
        elif not self._overrides_cleared:
            self._overrides_cleared = True
            try:
                self.client.teardown_overrides()
            except Exception:  # noqa: BLE001
                pass
        self._watchdog_up = bool(st.get("watchdog_running"))
        self._reverse_ssh_up = bool(st.get("reverse_ssh_running"))
        self._reverse_ssh_port = int(st.get("reverse_ssh_listen") or 2222)
        self._socks_up = socks_up
        self._http_up = http_up
        self._pac_up = pac_up
        self._socks_port = int(st.get("socks_port") or self._socks_port or 1080)
        self._http_port = int(st.get("http_port") or 1088)
        self._pac_port = int(st.get("pac_port") or 1089)
        self._server_target = target
        self._scope = scope
        self._mode_label = "Корпоративный" if self._corporate else "VPN"
        self._tun_button_text = "TUN выкл" if tun else "TUN вкл"

        if self.paths.config_path.is_file():
            try:
                cfg = load_config(self.paths.config_path)
                server = cfg.get("server") or {}
                self._socks_port = int(server.get("local_socks_port") or 1080)
            except Exception:  # noqa: BLE001
                pass

        socks_s = f"SOCKS :{self._socks_port}"
        http_s = f"HTTP :{self._http_port}"
        if singbox and tun and socks_up:
            title, sub, color = (
                "Защищено",
                f"{target} · {socks_s} · TUN",
                _C_ACCENT,
            )
            power = "Отключить"
        elif singbox and not socks_up:
            title, sub, color = (
                "Сбой",
                f"процесс есть, {socks_s} молчит — смотрите журнал",
                _C_DANGER,
            )
            power = "Отключить"
        elif singbox:
            title, sub, color = "Подключено", f"{target} · {socks_s} · {http_s}", _C_OK
            power = "Отключить"
        elif tun:
            title, sub, color = "TUN", "Без VLESS", _C_WARN
            power = "Отключить"
        elif ks_on:
            title, sub, color = (
                "Нет сети",
                "Kill switch блокирует интернет — отключите VPN, чтобы снять блок",
                _C_WARN,
            )
            power = "Отключить"
        else:
            title, sub, color = (
                ("Нет конфига", "Загрузите config.json или .enc", _C_MUTED)
                if not self._config_ready
                else ("Отключено", "Нажмите «Подключить»", _C_MUTED)
            )
            power = "Загрузить конфиг" if not self._config_ready else "Подключить"

        self._status_title = title
        self._status_sub = sub
        self._status_color = color
        self._power_text = power

        self.activeChanged.emit()
        self.tunChanged.emit()
        self.killSwitchOnChanged.emit()
        self.singboxUpChanged.emit()
        self.watchdogUpChanged.emit()
        self.reverseSshUpChanged.emit()
        self.reverseSshPortChanged.emit()
        self.socksUpChanged.emit()
        self.httpUpChanged.emit()
        self.pacUpChanged.emit()
        self.socksPortChanged.emit()
        self.httpPortChanged.emit()
        self.pacPortChanged.emit()
        self.serverTargetChanged.emit()
        self.scopeChanged.emit()
        self.modeLabelChanged.emit()
        self.tunButtonTextChanged.emit()
        self.statusTitleChanged.emit()
        self.statusSubChanged.emit()
        self.statusColorChanged.emit()
        self.powerTextChanged.emit()


def _settings_defaults() -> dict[str, Any]:
    return {
        "corporate": False,
        "useProxy": False,
        "socksScope": "full",
        "tunAuto": True,
        "killSwitch": True,
        "httpBridgePort": "1088",
        "corporateProxy": "",
        "serverHost": "",
        "serverPort": "443",
        "serverSocks": "1080",
        "proxyBypass": "",
        "proxyBypassVia": "direct",
        "trUuid": "",
        "trPublicKey": "",
        "trShortId": "",
        "trServerName": "www.cloudflare.com",
        "trPort": "443",
        "reverseSsh": False,
        "reverseSshListen": "2222",
        "reverseSshVpsUser": "root",
        "reverseSshVpsPort": "22",
    }


def qml_dir() -> Path:
    here = Path(__file__).resolve().parent / "qml"
    if here.is_dir():
        return here
    from desktop.paths import bundle_dir

    bundled = bundle_dir() / "desktop" / "ui" / "qml"
    return bundled
