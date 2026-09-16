"""Connect / disconnect / TUN lifecycle for OpsClient."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from desktop import procutil
from desktop.client_util import HELPER_CMDLINE, pid_from_file
from desktop.config_io import (
    get_http_bridge_port,
    get_kill_switch,
    get_local_socks_port,
    get_pac_listen_port,
    get_server,
    get_server_host,
    get_sing_box_path,
    get_socks_scope,
    get_tun_elevate,
    get_tun_enabled,
    get_tun_mtu,
    get_vps_proxy_ports,
    infer_corporate,
    resolve_corporate_proxy,
    update_config_key,
)
from desktop.kill_switch import allow_ips as kill_switch_allow_ips
from desktop.kill_switch import apply as apply_kill_switch
from desktop.kill_switch import clear as clear_kill_switch
from desktop.kill_switch import install_commands as kill_switch_install_cmds
from desktop.kill_switch import is_applied as kill_switch_is_applied
from desktop.kill_switch import planned_pin_commands as kill_switch_pin_cmds
from desktop.kill_switch import pin_underlay as pin_kill_switch_underlay
from desktop.kill_switch import prefer_tun_ipv4, suppress_underlay_ipv6
from desktop.kill_switch import remember_plan as remember_kill_switch_plan
from desktop.kill_switch import state_path as kill_switch_state_path
from desktop.singbox_mode import (
    amneziawg_opts,
    choose_dial,
    dial_label,
    effective_tun_mtu,
    require_transport,
    resolve_dial_bundle,
    underlay_keep_hosts,
)
from desktop.tun import (
    apply_tun_iface_mtu,
    default_route_lines,
    foreign_vpn_live,
    foreign_vpn_processes,
    gateway_via_dest,
    ensure_tun_split_default,
    leftover_vpn_default_cmds,
    leftover_vpn_ifaces,
    our_tun_split_leftover,
    reclaim_tun_default,
    run_leftover_vpn_default_cmds,
    run_route_cmds,
    stale_default_cmds,
    tun_owns_default,
    wait_tun_iface,
)
from lib.netutil import port_open


class ConnectionOps:
    def start_singbox_mode(self) -> None:
        """VLESS+Reality through Squid (sing-box)."""
        self.reload_env()
        cfg = self.config()
        server = get_server(cfg)
        host = get_server_host(cfg)
        if "YOUR_VPS" in host or not host:
            raise RuntimeError("Set real server.host in config.json")

        transport = require_transport(cfg)
        port = int(transport.get("port") or server.get("port") or 443)
        office_proxy = resolve_corporate_proxy(cfg)
        dial = choose_dial(transport, office=bool(office_proxy))
        awg = amneziawg_opts(transport) if dial == "amneziawg" else None
        if dial == "amneziawg" and not awg:
            raise RuntimeError("AmneziaWG: укажите ключи в Настройках")
        if office_proxy and dial == "vless-reality":
            self.log(f"Probing CONNECT {host}:{port} via proxy...")
            if self.probe(host, port) != 0:
                raise RuntimeError(
                    f"Корпоративный прокси {office_proxy} не пустил CONNECT "
                    f"{host}:{port}. Проверьте, что вы в сети организации, "
                    "или выберите AWG."
                )

        # Avoid fighting leftover TUN processes
        try:
            if self.tun.running():
                self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"stop legacy TUN: {exc}")

        socks_port = get_local_socks_port(cfg)
        http_port = get_http_bridge_port()
        self.reap_leftovers(socks_port=socks_port)

        proxy_url = None
        if port_open("127.0.0.1", http_port):
            proxy_url = f"http://127.0.0.1:{http_port}"
        sing_box_path = get_sing_box_path(cfg)
        if dial == "amneziawg":
            exe = self.singbox.find_awg_sing_box()
            if not exe or not self.tun.awg_version_ok():
                self.log("sing-box AWG — скачиваю зафиксированную сборку…")
                exe = self.singbox.ensure_awg_downloaded(proxy_url=proxy_url)
            sing_box_path = str(exe)
        elif not self.singbox.find_sing_box(sing_box_path):
            self.log("sing-box missing — downloading…")
            self.download_sing_box()
            sing_box_path = get_sing_box_path(cfg)

        bypass = [str(h) for h in cfg.get("proxy_bypass") or [] if h]
        if not bypass:
            bypass = ["*.local", "*.lan"]
        vpn_hosts = [str(h) for h in cfg.get("blocked_hosts") or [] if h]
        kill_switch = get_kill_switch()
        enable_tun = get_tun_enabled() or kill_switch
        if kill_switch and not get_tun_enabled():
            self.log("kill switch: поднимаю TUN")
        leftover_cmds = self._log_foreign_vpn(host, office_proxy)
        prelude: list[str] = []
        allow = self._kill_switch_hosts(cfg)
        # Home Windows: kill-switch /1 via 127.0.0.1 and TUN inbound both
        # steal AWG UDP (CONNECT still looks fine). Defer KS until HTTPS works.
        udp_dial = dial == "amneziawg"
        defer_win = sys.platform == "win32" and (not bool(office_proxy) or udp_dial)
        self._defer_win_ks = bool(defer_win and kill_switch and udp_dial)
        self._hold_watchdog = True
        awg_defer_tun = bool(awg and enable_tun)
        start_tun = enable_tun and not awg_defer_tun
        start_ks = kill_switch and not self._defer_win_ks and not awg_defer_tun
        if (
            self._defer_win_ks
            and kill_switch_is_applied()
            and not getattr(self, "_fail_closed", False)
        ):
            self.log(
                "kill switch: снимаю до проверки выхода — иначе UDP до VPS не проходит"
            )
            clear_kill_switch(var_dir=self.paths.var_dir, log=self.log)
        if start_ks:
            prelude = leftover_cmds + self._ensure_kill_switch(cfg)
        elif enable_tun or start_tun:
            remember_kill_switch_plan(self.paths.var_dir, allow)
            prelude = leftover_cmds
        else:
            prelude = leftover_cmds
            if allow:
                remember_kill_switch_plan(self.paths.var_dir, allow)
        postlude = (
            kill_switch_pin_cmds(self.paths.var_dir, allow) if start_tun else []
        )
        self._pending_awg_tun = awg_defer_tun
        self._awg_tun_kwargs = None
        if awg_defer_tun:
            self._awg_tun_kwargs = {
                "server_host": host,
                "transport": transport,
                "corporate_proxy": office_proxy,
                "socks_port": socks_port,
                "http_port": http_port,
                "sing_box_path": sing_box_path,
                "elevate": get_tun_elevate(),
                "bypass_hosts": bypass,
                "vpn_hosts": vpn_hosts,
                "mtu": get_tun_mtu(cfg),
                "vps_proxy_ports": get_vps_proxy_ports(cfg),
                "kill_switch": kill_switch,
            }
            self.log("AmneziaWG: сначала handshake без TUN")
        self.singbox.start(
            server_host=host,
            transport=transport,
            corporate_proxy=office_proxy,
            socks_port=socks_port,
            http_port=http_port,
            enable_tun=start_tun,
            sing_box_path=sing_box_path,
            elevate=get_tun_elevate() if start_tun else False,
            bypass_hosts=bypass,
            vpn_hosts=vpn_hosts,
            mtu=get_tun_mtu(cfg),
            vps_proxy_ports=get_vps_proxy_ports(cfg),
            force_restart=True,
            kill_switch=start_ks,
            prelude_cmds=prelude,
            postlude_cmds=postlude,
        )
        self.set_git_singbox(cfg, http_port)
        self._maybe_start_reverse_ssh(cfg)
        state = {
            "mode": "singbox",
            "scope": get_socks_scope(),
            "tun": start_tun,
            "socks": f"socks5h://127.0.0.1:{socks_port}",
            "http": f"http://127.0.0.1:{http_port}",
            "pac": f"http://127.0.0.1:{get_pac_listen_port()}/proxy.pac",
            "started": datetime.now(timezone.utc).isoformat(),
        }
        self.paths.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.log(
            f"sing-box готов: scope={get_socks_scope()} tun={int(start_tun)} "
            f"socks=127.0.0.1:{socks_port} http=127.0.0.1:{http_port}"
        )
        if office_proxy and dial == "vless-reality":
            # Office blocks raw :443 to the VPS; CONNECT via Squid was already probed.
            self.log(
                f"диагностика: прямой TCP {host}:{port} не проверяем — "
                "VLESS идёт через корпоративный прокси"
            )
        elif awg:
            self.log(
                f"диагностика: AmneziaWG UDP {host}:{awg['port']} — "
                "TCP Reality не проверяем"
            )
        else:
            try:
                sock = socket.create_connection((host, port), timeout=3)
                sock.close()
                self.log(f"диагностика: TCP {host}:{port} с этой NIC — OK")
            except OSError as exc:
                self.log(f"диагностика: TCP {host}:{port} с этой NIC — FAIL ({exc})")
        self._log_singbox_tail("после запуска")
        if start_tun and procutil.is_admin() and allow:
            pin_kill_switch_underlay(
                allow, var_dir=self.paths.var_dir, log=self.log, elevate=False
            )
            threading.Thread(
                target=self._pin_underlay_later,
                args=(allow,),
                daemon=True,
            ).start()
        self._pending_win_tun = bool(
            start_tun and procutil.is_admin() and sys.platform == "win32"
        )
        self._pending_allow = allow
        threading.Thread(
            target=self._probe_exit,
            args=(
                socks_port,
                0.25 if awg else (0.4 if start_tun else 0.0),
            ),
            daemon=True,
        ).start()


    def _upgrade_awg_to_tun(self) -> None:
        """After AWG handshake, restart sing-box with TUN + routes."""
        kw = getattr(self, "_awg_tun_kwargs", None)
        self._awg_tun_kwargs = None
        self._pending_awg_tun = False
        if not kw:
            return
        self.log("AmneziaWG: handshake есть — поднимаю TUN")
        cfg = self.config()
        allow = list(getattr(self, "_pending_allow", []) or [])
        kill_switch = bool(kw.pop("kill_switch", get_kill_switch()))
        start_ks = kill_switch and not getattr(self, "_defer_win_ks", False)
        prelude: list[str] = []
        if start_ks:
            prelude = self._ensure_kill_switch(cfg)
        elif allow:
            remember_kill_switch_plan(self.paths.var_dir, allow)
        postlude = kill_switch_pin_cmds(self.paths.var_dir, allow) if allow else []
        try:
            self.singbox.start(
                enable_tun=True,
                force_restart=True,
                kill_switch=start_ks,
                prelude_cmds=prelude,
                postlude_cmds=postlude,
                **kw,
            )
        except Exception:
            self.log("AmneziaWG: TUN не поднялся — оставляю подключение без него")
            self.singbox.start(
                enable_tun=False,
                force_restart=True,
                kill_switch=False,
                prelude_cmds=[],
                postlude_cmds=[],
                **kw,
            )
            raise
        if self.paths.state_path.is_file():
            try:
                state = json.loads(self.paths.state_path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    state["tun"] = True
                    self.paths.state_path.write_text(
                        json.dumps(state, indent=2), encoding="utf-8"
                    )
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        self._pending_win_tun = bool(
            procutil.is_admin() and sys.platform == "win32"
        )
        if self._pending_win_tun and allow:
            try:
                self._install_win_tun_routes(allow)
            except Exception as exc:  # noqa: BLE001
                self.log(f"TUN после AWG: {exc}")
            self._pending_win_tun = False
        if getattr(self, "_defer_win_ks", False) and kill_switch:
            try:
                apply_kill_switch(
                    allow,
                    var_dir=self.paths.var_dir,
                    log=self.log,
                    blackhole=False,
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"kill switch после AWG: {exc}")
            self._defer_win_ks = False

    def _stop_session_core(self, *, scan_helpers: bool = False) -> Exception | None:
        stop_err: Exception | None = None
        try:
            self.reverse_ssh.stop(scan_cmdline=scan_helpers)
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh stop: {exc}")
        try:
            self.singbox.stop()
        except Exception as exc:  # noqa: BLE001
            stop_err = exc
            self.log(f"sing-box не остановился: {exc}")
        self.stop_pac_server()
        try:
            self.stop_http_bridge()
        except Exception as exc:  # noqa: BLE001
            self.log(f"http-bridge stop: {exc}")
        return stop_err

    def stop_singbox_mode(self, *, teardown: bool = True) -> None:
        self._stop_session_core()
        if teardown:
            self.teardown_overrides_if_dirty()
        self.paths.state_path.unlink(missing_ok=True)


    def _vpn_process_up(self) -> bool:
        try:
            if self.singbox.running():
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.tun.running():
                return True
        except Exception:  # noqa: BLE001
            pass
        return False


    def _helper_traces(self) -> bool:
        return any(
            path.is_file()
            for path in (
                self.paths.bridge_pid,
                self.paths.pac_pid,
                self.paths.watchdog_pid,
                self.reverse_ssh.pid_path,
                self.paths.state_path,
            )
        )


    def reap_leftovers(self, *, socks_port: int | None = None) -> None:
        """Free HTTP/PAC/SOCKS leftovers before starting sing-box."""
        socks = int(socks_port if socks_port is not None else get_local_socks_port())
        http_port = get_http_bridge_port()
        pac_port = get_pac_listen_port()
        by_port = procutil.pids_listening_on_many(
            [socks, http_port, pac_port], cache=False
        )
        targets: list[int] = []
        had_pid = False
        for path in (self.paths.bridge_pid, self.paths.pac_pid):
            pid = pid_from_file(path, unlink=True)
            if pid:
                had_pid = True
                targets.append(pid)
        for pids in by_port.values():
            targets.extend(pids)
        if had_pid or any(by_port.values()) or self._helper_traces():
            for extra in procutil.pids_cmdline_match_many(
                (
                    "desktop pac-serve",
                    "-m desktop pac-serve",
                ),
                cache=False,
            ).values():
                targets.extend(extra)
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"leftover pid={pid} stopped")


    def _reap_helpers(self, *, scan_cmdline: bool = False) -> None:
        """Stop leftover helper children. Cmdline scan only when asked or pid files remain."""
        ports: list[int] = []
        if self._pac_server is None:
            ports.append(get_pac_listen_port())
        ports.append(get_http_bridge_port())
        by_port = (
            procutil.pids_listening_on_many(ports, cache=False) if ports else {}
        )
        targets: list[int] = []
        had_pid = False
        for path in (
            self.paths.watchdog_pid,
            self.paths.pac_pid,
            self.paths.bridge_pid,
            self.reverse_ssh.pid_path,
        ):
            pid = pid_from_file(path, unlink=True)
            if pid:
                had_pid = True
                targets.append(pid)
        for pids in by_port.values():
            targets.extend(pids)
        if had_pid or scan_cmdline:
            for extra in procutil.pids_cmdline_match_many(
                HELPER_CMDLINE, cache=False
            ).values():
                targets.extend(extra)
        killed = procutil.kill_pids(targets, exclude=os.getpid())
        for pid in killed:
            self.log(f"helper pid={pid} stopped")


    def enable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
        if persist:
            update_config_key(self.paths.config_path, "tun.enabled", True)
            self.log("tun.enabled=true записан в config.json")
        # Restart sing-box process with TUN inbound
        self.start_singbox_mode()


    def disable_tun(self, *, persist: bool = True) -> None:
        self.reload_env()
        from desktop.leak_shield import restore as restore_leak_shield

        try:
            restore_leak_shield(var_dir=self.paths.var_dir, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"leak shield off: {exc}")
        if persist and get_kill_switch():
            update_config_key(self.paths.config_path, "kill_switch", False)
            self.log("kill switch выключен вместе с TUN")
        if persist:
            update_config_key(self.paths.config_path, "tun.enabled", False)
            self.log("tun.enabled=false записан в config.json")
        if self.singbox.running() or (
            self.paths.state_path.is_file()
            and "singbox"
            in self.paths.state_path.read_text(encoding="utf-8-sig", errors="ignore")
        ):
            self.start_singbox_mode()


    def _install_win_tun_routes(self, allow: list[str], *, strict: bool = True) -> None:
        """After TUN adapter exists: steal traffic without auto_route."""
        idx = wait_tun_iface(timeout=20.0)
        if not idx:
            self.log("TUN-адаптер так и не появился — split default не ставлю")
            return
        pin_kill_switch_underlay(
            allow, var_dir=self.paths.var_dir, log=self.log, elevate=False
        )
        run_leftover_vpn_default_cmds()
        try:
            cfg = self.config()
            office = bool(resolve_corporate_proxy(cfg))
            tr = require_transport(cfg)
            dial = choose_dial(tr, office=office)
            awg = amneziawg_opts(tr) if dial == "amneziawg" else None
            apply_tun_iface_mtu(
                idx,
                effective_tun_mtu(
                    get_tun_mtu(cfg),
                    awg_mtu=int(awg["mtu"]) if awg else None,
                    via_office_proxy=office and not awg,
                ),
                log=self.log,
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"TUN MTU: {exc}")
        ok, detail = ensure_tun_split_default(idx)
        self.log(detail)
        if not ok:
            self.log(
                "TUN split: маршруты /1 не в таблице — браузер пойдёт мимо VPN "
                "(GitHub/IPv6/второй NIC)"
            )
        else:
            prefer_tun_ipv4(idx, var_dir=self.paths.var_dir, log=self.log)
            suppress_underlay_ipv6(
                var_dir=self.paths.var_dir, tun_idx=idx, log=self.log
            )
        pin_kill_switch_underlay(
            allow, var_dir=self.paths.var_dir, log=self.log, elevate=False
        )
        from desktop.leak_shield import apply as apply_leak_shield

        try:
            apply_leak_shield(var_dir=self.paths.var_dir, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"leak shield: {exc}")
        if tun_owns_default():
            return
        self.log("TUN не владеет default — снимаю чужой /1 и ставлю split снова")
        reclaim_tun_default(idx)
        if tun_owns_default():
            return
        self.log("чужой VPN перекрыл TUN — закрываю underlay")
        apply_kill_switch(
            allow, var_dir=self.paths.var_dir, log=self.log, blackhole=True
        )
        if not strict:
            return
        raise RuntimeError(
            "Чужой VPN перекрыл маршруты TUN. Отключите второй VPN и подключитесь снова."
        )


    def _pin_underlay_later(self, allow: list[str]) -> None:
        for i, wait in enumerate((0.15, 0.4)):
            time.sleep(wait)
            pin_kill_switch_underlay(
                allow,
                var_dir=self.paths.var_dir,
                log=self.log if i == 0 else (lambda _m: None),
                elevate=False,
            )


    def _log_foreign_vpn(self, host: str, office_proxy: str) -> list[str]:
        leftover = leftover_vpn_ifaces()
        leftover_cmds = leftover_vpn_default_cmds()
        keep_gw = gateway_via_dest(host) or ""
        stale = stale_default_cmds(keep_gw) if keep_gw else []
        leftover_cmds = list(dict.fromkeys(leftover_cmds + stale))
        idle_procs = foreign_vpn_processes()
        live = leftover or foreign_vpn_live()
        if leftover:
            names = ", ".join(name for _idx, name in leftover)
            self.log(
                f"чужой туннель ещё поднят ({names}) — снимаю его default, "
                f"служба остаётся"
            )
        elif live:
            self.log(
                "чужой туннель ещё поднят ("
                + ", ".join(str(x) for x in live)
                + ") — снимаю его default"
            )
        elif idle_procs:
            self.log(
                "служба "
                + ", ".join(str(x) for x in idle_procs)
                + " установлена, туннель не поднят"
            )
        for row in default_route_lines():
            self.log(f"default: {row}")
        if stale:
            shown = [c for c in stale if "-p" not in c]
            self.log("лишний default второго VPN сниму: " + "; ".join(shown))
        if leftover_cmds and procutil.is_admin():
            run_route_cmds(leftover_cmds)
            leftover_cmds = []
        elif leftover_cmds:
            self.log("чужой default сниму вместе с UAC")
        return leftover_cmds

    def _kill_switch_hosts(self, cfg: dict[str, Any]) -> list[str]:
        host = get_server_host(cfg)
        proxy = resolve_corporate_proxy(cfg)
        udp = False
        try:
            _dial, awg = resolve_dial_bundle(
                require_transport(cfg), office=bool(proxy)
            )
            udp = bool(awg)
        except Exception:  # noqa: BLE001
            pass
        return kill_switch_allow_ips(
            *underlay_keep_hosts(host, proxy, udp_dial=udp)
        )


    def _ensure_kill_switch(self, cfg: dict[str, Any]) -> list[str]:
        """Apply OS routes now if admin; otherwise return prelude for UAC wrapper."""
        allow = self._kill_switch_hosts(cfg)
        if procutil.is_admin() or kill_switch_is_applied():
            apply_kill_switch(
                allow, var_dir=self.paths.var_dir, log=self.log, blackhole=False
            )
            return []
        cmds = kill_switch_install_cmds(allow)
        if cmds:
            remember_kill_switch_plan(self.paths.var_dir, allow)
            self.log("kill switch: маршруты поставлю вместе с UAC для TUN")
        return cmds


    def enable(self, *, spawn_watchdog: bool = True) -> None:
        self.reload_env()
        tun = get_tun_enabled() or get_kill_switch()
        ks = get_kill_switch()
        cfg = self.config()
        office = bool(resolve_corporate_proxy(cfg))
        try:
            dial = choose_dial(require_transport(cfg), office=office)
        except Exception:  # noqa: BLE001
            dial = "vless-reality"
        label = dial_label(dial)
        self.log(
            f"подключение: {label}, TUN={'вкл' if tun else 'выкл'}"
            f", защита при обрыве={'вкл' if ks else 'выкл'}"
        )
        self._exit_probe_error = None
        self._exit_probe_hint = None
        self._want_watchdog = spawn_watchdog
        self.start_singbox_mode()
        if spawn_watchdog:
            if procutil.is_admin() and not self._inprocess_helpers:
                self.stop_watchdog_daemon()
            self.ensure_watchdog_daemon()


    def shutdown(self) -> None:
        """Quit path: disable once and skip atexit teardown."""
        self._atexit_done = True
        try:
            self.disable()
        except Exception:  # noqa: BLE001
            try:
                self.teardown_overrides_if_dirty()
            except Exception:  # noqa: BLE001
                pass


    def disable(self) -> None:
        self.reload_env()
        self.log("отключение VPN…")
        self._exit_probe_error = None
        self._exit_probe_hint = None
        self._fail_closed = False
        self._pending_awg_tun = False
        self._awg_tun_kwargs = None
        try:
            self.stop_watchdog_daemon()
        except Exception as exc:  # noqa: BLE001
            self.log(f"watchdog stop: {exc}")
        stop_err = self._stop_session_core(scan_helpers=True)
        try:
            self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"legacy TUN: {exc}")

        ks_thread: threading.Thread | None = None
        need_routes = (
            kill_switch_state_path(self.paths.var_dir).is_file()
            or kill_switch_is_applied(force=True)
            or our_tun_split_leftover()
        )
        if need_routes:
            def _clear_ks() -> None:
                try:
                    clear_kill_switch(var_dir=self.paths.var_dir, log=self.log)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"kill switch off: {exc}")

            ks_thread = threading.Thread(
                target=_clear_ks, name="ergoms-kill-switch-clear", daemon=True
            )
            ks_thread.start()

        self.teardown_overrides()
        self.paths.state_path.unlink(missing_ok=True)
        self._reap_helpers(scan_cmdline=True)
        if ks_thread is not None:
            ks_thread.join(timeout=8.0)
        else:
            from desktop.leak_shield import restore as restore_leak_shield

            try:
                restore_leak_shield(var_dir=self.paths.var_dir, log=self.log)
            except Exception as exc:  # noqa: BLE001
                self.log(f"leak shield off: {exc}")

        procutil.invalidate_proc_cache()
        leftover = self.singbox.pid()
        if leftover:
            raise RuntimeError(
                f"sing-box pid={leftover} всё ещё работает. "
                "При TUN нужен UAC, чтобы его остановить."
            )
        if stop_err:
            raise stop_err
        self._atexit_done = True
        self.log(
            "VPN отключён: sing-box, watchdog, PAC/git/Docker и маршруты сброшены"
        )

