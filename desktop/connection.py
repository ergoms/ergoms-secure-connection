"""Connect / disconnect / TUN lifecycle for OpsClient."""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from desktop import procutil
from desktop.client_util import HELPER_CMDLINE, pid_from_file
from desktop.config_io import (
    get_http_bridge_port,
    get_local_socks_port,
    get_pac_listen_port,
    get_tun_mtu,
    resolve_corporate_proxy,
)
from desktop.kill_switch import apply as apply_kill_switch
from desktop.kill_switch import install_commands as kill_switch_install_cmds
from desktop.kill_switch import is_applied as kill_switch_is_applied
from desktop.kill_switch import pin_underlay as pin_kill_switch_underlay
from desktop.kill_switch import prefer_tun_ipv4, suppress_underlay_ipv6
from desktop.kill_switch import remember_plan as remember_kill_switch_plan
from desktop.lifecycle.actions import Action
from desktop.singbox_mode import (
    amneziawg_opts,
    choose_dial,
    effective_tun_mtu,
    require_transport,
    resolve_dial_bundle,
    underlay_forget_hosts,
    underlay_keep_hosts,
)
from desktop.tun import (
    apply_tun_iface_mtu,
    default_route_lines,
    ensure_tun_split_default,
    foreign_vpn_live,
    foreign_vpn_processes,
    gateway_via_dest,
    leftover_vpn_default_cmds,
    leftover_vpn_ifaces,
    reclaim_tun_default,
    run_leftover_vpn_default_cmds,
    run_route_cmds,
    stale_default_cmds,
    tun_owns_default,
    wait_tun_iface,
)


class ConnectionOps:
    def _pipeline(self):
        from desktop.lifecycle.pipeline import ConnectionPipeline

        pipe = getattr(self, "pipeline", None)
        if pipe is None:
            pipe = ConnectionPipeline(self)
            self.pipeline = pipe
        return pipe

    def start_singbox_mode(self) -> None:
        self._pipeline().run(Action.CONNECT, spawn_watchdog=False)

    def stop_singbox_mode(self, *, teardown: bool = True) -> None:
        self._stop_session_core()
        if teardown:
            self.teardown_overrides_if_dirty()
        self.paths.state_path.unlink(missing_ok=True)

    def enable(self, *, spawn_watchdog: bool = True) -> None:
        self._pipeline().run(Action.CONNECT, spawn_watchdog=spawn_watchdog)

    def disable(self) -> None:
        self._pipeline().run(Action.DISCONNECT)

    def reconnect(self, *, spawn_watchdog: bool = True) -> None:
        self._pipeline().run(Action.RECONNECT, spawn_watchdog=spawn_watchdog)

    def enable_tun(self, *, persist: bool = True) -> None:
        self._pipeline().run(Action.TUN_ON, persist=persist)

    def disable_tun(self, *, persist: bool = True) -> None:
        self._pipeline().run(Action.TUN_OFF, persist=persist)

    def shutdown(self) -> None:
        self._atexit_done = True
        try:
            self.disable()
        except Exception:  # noqa: BLE001
            try:
                self.teardown_overrides_if_dirty()
            except Exception:  # noqa: BLE001
                pass

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

    def _vpn_process_up(self) -> bool:
        try:
            return bool(self.singbox.running())
        except Exception:  # noqa: BLE001
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

    def _install_win_tun_routes(self, allow: list[str], *, strict: bool = True) -> bool:
        """After TUN adapter exists: steal traffic without auto_route."""
        idx = wait_tun_iface(timeout=20.0)
        if not idx:
            self.log("TUN-адаптер так и не появился — split default не ставлю")
            return False
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
        from desktop.leak_shield import apply as apply_leak_shield

        try:
            apply_leak_shield(var_dir=self.paths.var_dir, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"leak shield: {exc}")
        if tun_owns_default():
            return True
        self.log("TUN не владеет default — снимаю чужой /1 и ставлю split снова")
        reclaim_tun_default(idx)
        if tun_owns_default():
            return True
        self.log("чужой VPN перекрыл TUN — закрываю underlay")
        apply_kill_switch(
            allow, var_dir=self.paths.var_dir, log=self.log, blackhole=True
        )
        if not strict:
            return False
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
        return self._kill_switch_pins(cfg)[0]

    def _kill_switch_pins(self, cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
        from desktop.config_io import get_server_host
        from desktop.kill_switch import allow_ips as kill_switch_allow_ips

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
        return (
            kill_switch_allow_ips(*underlay_keep_hosts(host, proxy, udp_dial=udp)),
            kill_switch_allow_ips(*underlay_forget_hosts(host, proxy, udp_dial=udp)),
        )

    def _ensure_kill_switch(self, cfg: dict[str, Any]) -> list[str]:
        allow, forget = self._kill_switch_pins(cfg)
        if procutil.is_admin() or kill_switch_is_applied():
            apply_kill_switch(
                allow,
                var_dir=self.paths.var_dir,
                log=self.log,
                blackhole=False,
                forget=forget,
            )
            return []
        cmds = kill_switch_install_cmds(allow, forget=forget)
        if cmds:
            remember_kill_switch_plan(self.paths.var_dir, allow, forget=forget)
            self.log("kill switch: маршруты поставлю вместе с UAC для TUN")
        return cmds
