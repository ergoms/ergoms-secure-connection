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
from desktop.kill_switch import prefer_tun_ipv4
from desktop.kill_switch import remember_plan as remember_kill_switch_plan
from desktop.kill_switch import state_path as kill_switch_state_path
from desktop.singbox_mode import (
    amneziawg_opts,
    choose_dial,
    dial_label,
    hysteria2_opts,
    require_transport,
)
from desktop.tun import (
    default_route_lines,
    foreign_vpn_live,
    foreign_vpn_processes,
    gateway_via_dest,
    ensure_tun_split_default,
    leftover_vpn_default_cmds,
    leftover_vpn_ifaces,
    stale_default_cmds,
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
        hy = hysteria2_opts(transport) if dial == "hysteria2" else None
        awg = amneziawg_opts(transport) if dial == "amneziawg" else None
        if dial == "hysteria2" and not hy:
            raise RuntimeError("Hysteria2: укажите пароль в Настройках")
        if dial == "amneziawg" and not awg:
            raise RuntimeError("AmneziaWG: укажите ключи в Настройках")
        if office_proxy and dial == "vless-reality":
            self.log(f"Probing CONNECT {host}:{port} via proxy...")
            if self.probe(host, port) != 0:
                raise RuntimeError(
                    f"Корпоративный прокси {office_proxy} не пустил CONNECT "
                    f"{host}:{port}. Проверьте, что вы в сети организации, "
                    "или выберите AWG / Hy2."
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

        if infer_corporate(cfg):
            bypass = [str(h) for h in cfg.get("proxy_bypass") or [] if h]
        else:
            bypass = ["*.local", "*.lan"]
        kill_switch = get_kill_switch()
        enable_tun = get_tun_enabled() or kill_switch
        if kill_switch and not get_tun_enabled():
            self.log("kill switch: поднимаю TUN")
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
                f"чужой туннель ещё поднят ({names}) — UDP с Ethernet, "
                f"маршруты {keep_gw or 'underlay'} не трогаю"
            )
        elif live:
            self.log(
                "чужой туннель ещё поднят ("
                + ", ".join(str(x) for x in live)
                + ") — UDP с Ethernet"
            )
        elif idle_procs:
            self.log(
                "служба "
                + ", ".join(str(x) for x in idle_procs)
                + " установлена, туннель не поднят"
            )
        for row in default_route_lines():
            self.log(f"default: {row}")
        home = not bool(office_proxy)
        if stale and not home:
            shown = [c for c in stale if "-p" not in c]
            self.log(
                "лишний default второго VPN сниму: " + "; ".join(shown)
            )
        if leftover_cmds and procutil.is_admin() and not home:
            for line in leftover_cmds:
                args = [p for p in line.split(" ") if p]
                procutil.run(args, timeout=8)
            leftover_cmds = []
        if home:
            leftover_cmds = []
        prelude: list[str] = []
        allow = self._kill_switch_hosts(cfg)
        # Home Windows: kill-switch /1 via 127.0.0.1 and TUN inbound both
        # kill Hysteria2 QUIC (CONNECT still looks fine). Bring Hy2 up on
        # the underlay first, then TUN + blackholes after HTTPS works.
        udp_dial = dial in ("hysteria2", "amneziawg")
        defer_win = sys.platform == "win32" and (not bool(office_proxy) or udp_dial)
        # Hy2 QUIC dies if TUN is up first. AWG binds underlay — TUN inbound
        # (auto_route off) can start immediately; split/KS after HTTPS.
        # Reality: apply KS immediately so a failed handshake cannot leak.
        self._defer_win_tun = bool(defer_win and enable_tun and dial == "hysteria2")
        self._defer_win_ks = bool(defer_win and kill_switch and udp_dial)
        self._hold_watchdog = True
        self._box_boot = {
            "server_host": host,
            "transport": transport,
            "corporate_proxy": office_proxy or "",
            "socks_port": socks_port,
            "http_port": http_port,
            "sing_box_path": sing_box_path,
            "bypass_hosts": bypass,
            "mtu": get_tun_mtu(cfg),
            "vps_proxy_ports": get_vps_proxy_ports(cfg),
        }
        start_tun = enable_tun and not self._defer_win_tun
        start_ks = kill_switch and not self._defer_win_ks
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
        if self._defer_win_tun:
            proto = "AmneziaWG" if dial == "amneziawg" else "Hysteria2"
            self.log(f"дом: сначала {proto} без TUN, маршруты поставлю после проверки")
        postlude = (
            kill_switch_pin_cmds(self.paths.var_dir, allow) if start_tun else []
        )
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
            mtu=get_tun_mtu(cfg),
            vps_proxy_ports=get_vps_proxy_ports(cfg),
            force_restart=True,
            kill_switch=start_ks,
            prelude_cmds=prelude,
            postlude_cmds=postlude,
        )
        self.set_git_singbox(cfg, http_port)
        # Home Hy2: SOCKS is dead until QUIC is up. Reverse SSH through
        # SOCKS here steals the handshake (same class as PAC).
        if not getattr(self, "_defer_win_tun", False):
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
        elif hy:
            self.log(
                f"диагностика: Hysteria2 UDP {host}:{hy['port']} — "
                "TCP Reality не проверяем, домашний DPI его глотает"
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
                1.2 if self._defer_win_tun else (0.25 if awg else (0.4 if start_tun else 0.0)),
            ),
            daemon=True,
        ).start()


    def stop_singbox_mode(self, *, teardown: bool = True) -> None:
        try:
            self.reverse_ssh.stop(scan_cmdline=False)
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh stop: {exc}")
        self.singbox.stop()
        self.stop_pac_server()
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


    def _reap_helpers(self) -> None:
        """Stop leftover helper children. Skip cmdline scan unless pid files remain."""
        ports: list[int] = []
        if self._pac_server is None:
            ports.append(get_pac_listen_port())
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
        if had_pid:
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


    def _install_win_tun_routes(self, allow: list[str]) -> None:
        """After TUN adapter exists: steal traffic without auto_route."""
        idx = wait_tun_iface(timeout=20.0)
        if not idx:
            self.log("TUN-адаптер так и не появился — split default не ставлю")
            return
        pin_kill_switch_underlay(
            allow, var_dir=self.paths.var_dir, log=self.log, elevate=False
        )
        for line in leftover_vpn_default_cmds():
            args = [p for p in line.split(" ") if p]
            procutil.run(args, timeout=8)
        ok, detail = ensure_tun_split_default(idx)
        self.log(detail)
        if not ok:
            self.log(
                "TUN split: маршруты /1 не в таблице — браузер пойдёт мимо VPN "
                "(GitHub/IPv6/второй NIC)"
            )
        else:
            prefer_tun_ipv4(idx, var_dir=self.paths.var_dir, log=self.log)
        pin_kill_switch_underlay(
            allow, var_dir=self.paths.var_dir, log=self.log, elevate=False
        )


    def _bring_up_win_tun(self) -> None:
        """Restart sing-box with TUN only after Hysteria2 HTTPS already works."""
        boot = dict(getattr(self, "_box_boot", None) or {})
        allow = list(getattr(self, "_pending_allow", []) or [])
        want_ks = bool(getattr(self, "_defer_win_ks", False))
        if not boot:
            self.log("TUN: нет параметров запуска — оставляю SOCKS")
            return
        self.log("выход живой — поднимаю TUN")
        remember_kill_switch_plan(self.paths.var_dir, allow)
        try:
            self.singbox.start(
                **boot,
                enable_tun=True,
                elevate=True,
                force_restart=True,
                kill_switch=False,
                prelude_cmds=[],
                postlude_cmds=(
                    kill_switch_pin_cmds(self.paths.var_dir, allow) if allow else []
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"TUN не поднялся ({exc}) — трафик через SOCKS")
            return
        if procutil.is_admin() and allow:
            pin_kill_switch_underlay(
                allow, var_dir=self.paths.var_dir, log=self.log, elevate=False
            )
        socks_port = int(boot.get("socks_port") or 1080)
        time.sleep(0.8)
        from desktop.watchdog import socks_https_probe

        err = socks_https_probe(socks_port, timeout=10.0)
        if err:
            self.log(
                f"TUN снова оборвал UDP ({err}) — возвращаю SOCKS без TUN"
            )
            try:
                self.singbox.start(
                    **boot,
                    enable_tun=False,
                    elevate=False,
                    force_restart=True,
                    kill_switch=False,
                    prelude_cmds=[],
                    postlude_cmds=[],
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"откат на SOCKS: {exc}")
            return
        self._install_win_tun_routes(allow)
        if want_ks:
            apply_kill_switch(
                allow, var_dir=self.paths.var_dir, log=self.log, blackhole=False
            )
        try:
            raw = self.paths.state_path.read_text(encoding="utf-8")
            st = json.loads(raw) if raw else {}
            if isinstance(st, dict):
                st["tun"] = True
                self.paths.state_path.write_text(
                    json.dumps(st, indent=2), encoding="utf-8"
                )
        except (OSError, json.JSONDecodeError):
            pass
        self.log("TUN готов (split default после живого UDP)")


    def _pin_underlay_later(self, allow: list[str]) -> None:
        for i, wait in enumerate((0.15, 0.4)):
            time.sleep(wait)
            pin_kill_switch_underlay(
                allow,
                var_dir=self.paths.var_dir,
                log=self.log if i in (0, 3) else (lambda _m: None),
                elevate=False,
            )


    def _kill_switch_hosts(self, cfg: dict[str, Any]) -> list[str]:
        hosts = [get_server_host(cfg)]
        proxy = resolve_corporate_proxy(cfg)
        if proxy:
            hosts.append(proxy.split(":")[0])
        return kill_switch_allow_ips(*hosts)


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
        if spawn_watchdog and not getattr(self, "_defer_win_tun", False):
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
        self._stop_watchdog_inprocess()
        stop_err: Exception | None = None
        try:
            self.singbox.stop()
        except Exception as exc:  # noqa: BLE001
            stop_err = exc
            self.log(f"sing-box не остановился: {exc}")
        try:
            self.tun.stop()
        except Exception as exc:  # noqa: BLE001
            self.log(f"legacy TUN: {exc}")

        ks_thread: threading.Thread | None = None
        if kill_switch_state_path(self.paths.var_dir).is_file():
            def _clear_ks() -> None:
                try:
                    clear_kill_switch(var_dir=self.paths.var_dir, log=self.log)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"kill switch off: {exc}")

            ks_thread = threading.Thread(
                target=_clear_ks, name="ergoms-kill-switch-clear", daemon=True
            )
            ks_thread.start()

        try:
            self.reverse_ssh.stop(scan_cmdline=False)
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh stop: {exc}")
        self.stop_pac_server()
        self.teardown_overrides_if_dirty()
        self.paths.state_path.unlink(missing_ok=True)
        self._reap_helpers()
        if ks_thread is not None:
            ks_thread.join(timeout=8.0)

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
        self.log("VPN отключён: sing-box остановлен, PAC/git/Docker сброшены")

