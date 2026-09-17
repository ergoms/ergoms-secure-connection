"""Named connect / disconnect steps. Phase changes only go through the session."""

from __future__ import annotations

import json
import socket
import sys
import threading
from datetime import datetime, timezone
from typing import Any, cast

from desktop import procutil
from desktop.config_io import (
    get_kill_switch,
    get_pac_listen_port,
    update_config_key,
)
from desktop.kill_switch import clear as clear_kill_switch
from desktop.kill_switch import is_applied as kill_switch_is_applied
from desktop.kill_switch import is_sealed, lift_ipv4_blackholes
from desktop.kill_switch import pin_underlay as pin_kill_switch_underlay
from desktop.kill_switch import planned_pin_commands as kill_switch_pin_cmds
from desktop.kill_switch import remember_plan as remember_kill_switch_plan
from desktop.kill_switch import state_path as kill_switch_state_path
from desktop.lifecycle.actions import Action
from desktop.lifecycle.plan import ConnectPlan, resolve_connect_plan
from desktop.lifecycle.session import ConnectionSession, SessionBusy
from desktop.lifecycle.snapshot import Phase
from desktop.singbox_mode import dial_label
from desktop.tun import our_tun_split_leftover, remove_stale_tun_adapter
from lib.netutil import port_open


class ConnectionPipeline:
    def __init__(self, client: Any) -> None:
        self.client = client

    @property
    def session(self) -> ConnectionSession:
        return cast(ConnectionSession, self.client.session)

    def run(
        self,
        action: Action,
        *,
        spawn_watchdog: bool = True,
        persist: bool = True,
    ) -> None:
        try:
            with self.session.operation(action):
                if action is Action.DISCONNECT:
                    self.disconnect()
                elif action is Action.RECONNECT:
                    self.reconnect(spawn_watchdog=spawn_watchdog)
                elif action is Action.TUN_ON:
                    self.tun_on(persist=persist)
                elif action is Action.TUN_OFF:
                    self.tun_off(persist=persist)
                else:
                    self.connect(spawn_watchdog=spawn_watchdog)
        except SessionBusy:
            raise

    def connect(self, *, spawn_watchdog: bool = True) -> None:
        client = self.client
        client.reload_env()
        cfg = client.config()
        plan = resolve_connect_plan(cfg)
        self.session.transition(
            Phase.PREPARING,
            f"{dial_label(plan.dial)}, TUN={'вкл' if plan.tun_wanted else 'выкл'}, "
            f"защита={'вкл' if plan.ks_wanted else 'выкл'}",
            dial=plan.dial,
            tun_wanted=plan.tun_wanted,
            kill_switch=plan.ks_wanted,
            socks_port=plan.socks_port,
            http_port=plan.http_port,
            server_target=f"{plan.host}:{plan.port}",
            exit_probe_error="",
            exit_probe_hint="",
            fail_closed=False,
            want_watchdog=spawn_watchdog,
            hold_watchdog=True,
            pending_allow=tuple(plan.allow),
            defer_win_ks=plan.ks_deferred,
            pending_awg_tun=plan.tun_deferred,
        )
        client.log(
            f"подключение: {dial_label(plan.dial)}, TUN={'вкл' if plan.tun_wanted else 'выкл'}"
            f", защита при обрыве={'вкл' if plan.ks_wanted else 'выкл'}"
        )
        try:
            self._connect_steps(plan, cfg)
        except Exception:
            self.session.update(hold_watchdog=False)
            if self.session.snapshot.phase.is_connecting:
                self.session.transition(Phase.IDLE, "подключение не удалось")
            raise
        if spawn_watchdog:
            if procutil.is_admin() and not client._inprocess_helpers:
                client.stop_watchdog_daemon()
            client.ensure_watchdog_daemon()

    def reconnect(self, *, spawn_watchdog: bool = True) -> None:
        client = self.client
        client.reload_env()
        try:
            if is_sealed():
                lift_ipv4_blackholes(log=client.log)
        except Exception as exc:  # noqa: BLE001
            client.log(f"переподключение: чёрные /1: {exc}")
        client.log("переподключение — защиту при обрыве не снимаю")
        try:
            client.stop_singbox_mode(teardown=False)
        except Exception as exc:  # noqa: BLE001
            client.log(f"переподключение: stop: {exc}")
        self.connect(spawn_watchdog=spawn_watchdog)

    def disconnect(self) -> None:
        client = self.client
        client.reload_env()
        client.log("отключение VPN…")
        self.session.transition(
            Phase.STOPPING,
            "отключение",
            exit_probe_error="",
            exit_probe_hint="",
            fail_closed=False,
            pending_awg_tun=False,
            pending_win_tun=False,
            hold_watchdog=False,
            want_watchdog=False,
            defer_win_ks=False,
        )
        try:
            client.stop_watchdog_daemon()
        except Exception as exc:  # noqa: BLE001
            client.log(f"watchdog stop: {exc}")
        stop_err = client._stop_session_core(scan_helpers=True)

        ks_thread: threading.Thread | None = None
        need_routes = (
            kill_switch_state_path(client.paths.var_dir).is_file()
            or kill_switch_is_applied(force=True)
            or our_tun_split_leftover()
        )
        if need_routes:

            def _clear_ks() -> None:
                try:
                    clear_kill_switch(var_dir=client.paths.var_dir, log=client.log)
                except Exception as exc:  # noqa: BLE001
                    client.log(f"kill switch off: {exc}")

            ks_thread = threading.Thread(
                target=_clear_ks, name="ergoms-kill-switch-clear", daemon=True
            )
            ks_thread.start()

        client.teardown_overrides()
        client.paths.state_path.unlink(missing_ok=True)
        client._reap_helpers(scan_cmdline=True)
        if ks_thread is not None:
            ks_thread.join(timeout=8.0)
        else:
            from desktop.leak_shield import restore as restore_leak_shield

            try:
                restore_leak_shield(var_dir=client.paths.var_dir, log=client.log)
            except Exception as exc:  # noqa: BLE001
                client.log(f"leak shield off: {exc}")

        procutil.invalidate_proc_cache()
        leftover = client.singbox.pid()
        if leftover:
            self.session.transition(Phase.IDLE, "sing-box не остановился")
            raise RuntimeError(
                f"sing-box pid={leftover} всё ещё работает. "
                "При TUN нужен UAC, чтобы его остановить."
            )
        if stop_err:
            self.session.transition(Phase.IDLE, "ошибка остановки")
            raise stop_err
        client._atexit_done = True
        self.session.transition(
            Phase.IDLE,
            "VPN отключён",
            singbox_running=False,
            tun_running=False,
            tun_ready=False,
            socks_up=False,
            http_up=False,
            pac_up=False,
            kill_switch_applied=False,
        )
        client.log(
            "VPN отключён: sing-box, watchdog, PAC/git/Docker и маршруты сброшены"
        )

    def tun_on(self, *, persist: bool = True) -> None:
        client = self.client
        client.reload_env()
        if persist:
            update_config_key(client.paths.config_path, "tun.enabled", True)
            client.log("tun.enabled=true записан в config.json")
        self.connect(spawn_watchdog=True)

    def tun_off(self, *, persist: bool = True) -> None:
        client = self.client
        client.reload_env()
        from desktop.leak_shield import restore as restore_leak_shield

        try:
            restore_leak_shield(var_dir=client.paths.var_dir, log=client.log)
        except Exception as exc:  # noqa: BLE001
            client.log(f"leak shield off: {exc}")
        if persist and get_kill_switch():
            update_config_key(client.paths.config_path, "kill_switch", False)
            client.log("kill switch выключен вместе с TUN")
        if persist:
            update_config_key(client.paths.config_path, "tun.enabled", False)
            client.log("tun.enabled=false записан в config.json")
        if client.singbox.running() or (
            client.paths.state_path.is_file()
            and "singbox"
            in client.paths.state_path.read_text(encoding="utf-8-sig", errors="ignore")
        ):
            self.connect(spawn_watchdog=True)

    def _connect_steps(self, plan: ConnectPlan, cfg: dict[str, Any]) -> None:
        client = self.client
        self._stop_legacy_tun()
        client.reap_leftovers(socks_port=plan.socks_port)
        stale_tun = None
        if plan.start_tun and sys.platform == "win32":
            stale_tun = threading.Thread(
                target=remove_stale_tun_adapter,
                kwargs={"log": client.log, "hidden": True},
                name="ergoms-tun-cleanup",
                daemon=True,
            )
            stale_tun.start()
        if plan.office_proxy and plan.dial == "vless-reality":
            client.log(f"Probing CONNECT {plan.host}:{plan.port} via proxy...")
            if client.probe(plan.host, plan.port) != 0:
                raise RuntimeError(
                    f"Корпоративный прокси {plan.office_proxy} не пустил CONNECT "
                    f"{plan.host}:{plan.port}. Проверьте, что вы в сети организации, "
                    "или выберите AWG."
                )
        sing_box_path = self._ensure_binary(plan)
        leftover_cmds = client._log_foreign_vpn(plan.host, plan.office_proxy)
        prelude = self._arm_kill_switch(plan, cfg, leftover_cmds)
        postlude = (
            kill_switch_pin_cmds(client.paths.var_dir, plan.allow, forget=plan.forget)
            if plan.start_tun
            else []
        )
        client._awg_tun_kwargs = None
        if plan.tun_deferred:
            client._awg_tun_kwargs = {
                "server_host": plan.host,
                "transport": plan.transport,
                "corporate_proxy": plan.office_proxy,
                "socks_port": plan.socks_port,
                "http_port": plan.http_port,
                "sing_box_path": sing_box_path,
                "elevate": plan.elevate,
                "bypass_hosts": plan.bypass,
                "vpn_hosts": plan.vpn_hosts,
                "mtu": plan.mtu,
                "vps_proxy_ports": plan.vps_proxy_ports,
                "kill_switch": plan.ks_wanted,
            }
            client.log("AmneziaWG: сначала handshake без TUN")
        if stale_tun is not None:
            stale_tun.join(timeout=8.0)
        self.session.transition(Phase.LAUNCHING, "запуск sing-box")
        client.singbox.start(
            server_host=plan.host,
            transport=plan.transport,
            corporate_proxy=plan.office_proxy,
            socks_port=plan.socks_port,
            http_port=plan.http_port,
            enable_tun=plan.start_tun,
            sing_box_path=sing_box_path,
            elevate=plan.elevate if plan.start_tun else False,
            bypass_hosts=plan.bypass,
            vpn_hosts=plan.vpn_hosts,
            mtu=plan.mtu,
            vps_proxy_ports=plan.vps_proxy_ports,
            force_restart=True,
            kill_switch=plan.start_ks,
            prelude_cmds=prelude,
            postlude_cmds=postlude,
        )
        client.set_git_singbox(cfg, plan.http_port)
        client._maybe_start_reverse_ssh(cfg)
        self._write_state(plan)
        client.log(
            f"sing-box готов: scope={plan.scope} tun={int(plan.start_tun)} "
            f"socks=127.0.0.1:{plan.socks_port} http=127.0.0.1:{plan.http_port}"
        )
        self._log_dial_diag(plan)
        client._log_singbox_tail("после запуска")
        if plan.start_tun and procutil.is_admin() and plan.allow:
            pin_kill_switch_underlay(
                plan.allow,
                var_dir=client.paths.var_dir,
                log=client.log,
                elevate=False,
                forget=plan.forget,
            )
            threading.Thread(
                target=client._pin_underlay_later,
                args=(plan.allow,),
                daemon=True,
            ).start()
        pending_win_tun = bool(
            plan.start_tun and procutil.is_admin() and sys.platform == "win32"
        )
        next_phase = Phase.WAITING_TUN if plan.start_tun or plan.tun_deferred else Phase.WAITING_SOCKS
        self.session.transition(
            next_phase,
            "ждём SOCKS" if next_phase is Phase.WAITING_SOCKS else "ждём TUN / handshake",
            pending_win_tun=pending_win_tun,
            pending_awg_tun=plan.tun_deferred,
            pending_allow=tuple(plan.allow),
            defer_win_ks=plan.ks_deferred,
            singbox_running=True,
            tun_running=plan.start_tun,
            tun_wanted=plan.tun_wanted,
        )
        self.session.transition(Phase.PROBING, "проверка выхода")
        delay = 0.25 if plan.awg else (0.4 if plan.start_tun else 0.0)
        client._probe_exit(plan.socks_port, delay)
        self._finish_after_probe()

    def _stop_legacy_tun(self) -> None:
        return None

    def _ensure_binary(self, plan: ConnectPlan) -> str:
        client = self.client
        proxy_url = None
        if port_open("127.0.0.1", plan.http_port):
            proxy_url = f"http://127.0.0.1:{plan.http_port}"
        sing_box_path = plan.sing_box_path
        if plan.dial == "amneziawg":
            exe = client.singbox.find_awg_sing_box()
            if not exe or not client.tun.awg_version_ok():
                client.log("sing-box AWG — скачиваю зафиксированную сборку…")
                exe = client.singbox.ensure_awg_downloaded(proxy_url=proxy_url)
            return str(exe)
        if not client.singbox.find_sing_box(sing_box_path):
            client.log("sing-box missing — downloading…")
            client.download_sing_box()
            from desktop.config_io import get_sing_box_path

            return get_sing_box_path(client.config())
        return sing_box_path

    def _arm_kill_switch(
        self, plan: ConnectPlan, cfg: dict[str, Any], leftover_cmds: list[str]
    ) -> list[str]:
        client = self.client
        if (
            plan.ks_deferred
            and kill_switch_is_applied()
            and not self.session.snapshot.fail_closed
        ):
            client.log(
                "kill switch: снимаю до проверки выхода — иначе UDP до VPS не проходит"
            )
            clear_kill_switch(var_dir=client.paths.var_dir, log=client.log)
        if plan.start_ks:
            return leftover_cmds + list(client._ensure_kill_switch(cfg))
        remember_kill_switch_plan(client.paths.var_dir, plan.allow, forget=plan.forget)
        return leftover_cmds

    def _write_state(self, plan: ConnectPlan) -> None:
        state = {
            "mode": "singbox",
            "scope": plan.scope,
            "tun": plan.start_tun,
            "socks": f"socks5h://127.0.0.1:{plan.socks_port}",
            "http": f"http://127.0.0.1:{plan.http_port}",
            "pac": f"http://127.0.0.1:{get_pac_listen_port()}/proxy.pac",
            "started": datetime.now(timezone.utc).isoformat(),
        }
        self.client.paths.state_path.write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )

    def _log_dial_diag(self, plan: ConnectPlan) -> None:
        client = self.client
        if plan.office_proxy and plan.dial == "vless-reality":
            client.log(
                f"диагностика: прямой TCP {plan.host}:{plan.port} не проверяем — "
                "VLESS идёт через корпоративный прокси"
            )
        elif plan.awg:
            client.log(
                f"диагностика: AmneziaWG UDP {plan.host}:{plan.awg['port']} — "
                "TCP Reality не проверяем"
            )
        else:
            try:
                sock = socket.create_connection((plan.host, plan.port), timeout=3)
                sock.close()
                client.log(f"диагностика: TCP {plan.host}:{plan.port} с этой NIC — OK")
            except OSError as exc:
                client.log(
                    f"диагностика: TCP {plan.host}:{plan.port} с этой NIC — FAIL ({exc})"
                )

    def _finish_after_probe(self) -> None:
        snap = self.session.snapshot
        if snap.fail_closed:
            self.session.transition(
                Phase.FAIL_CLOSED,
                snap.exit_probe_error or "выхода нет",
                hold_watchdog=False,
            )
            return
        if snap.exit_probe_error:
            self.session.transition(
                Phase.DEGRADED,
                snap.exit_probe_error,
                hold_watchdog=False,
            )
            return
        self.session.transition(
            Phase.UP,
            "выход живой",
            hold_watchdog=False,
            exit_probe_error="",
            exit_probe_hint="",
            pending_awg_tun=False,
        )
