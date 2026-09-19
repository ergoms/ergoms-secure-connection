"""Exit / Squid probes for OpsClient."""

from __future__ import annotations

import time

from desktop import procutil
from desktop.config_io import (
    get_http_bridge_port,
    get_kill_switch,
    resolve_corporate_proxy,
)
from desktop.kill_switch import apply as apply_kill_switch
from desktop.kill_switch import is_sealed as kill_switch_is_sealed
from desktop.singbox_mode import (
    amneziawg_opts,
    choose_dial,
    require_transport,
)
from desktop.tun import foreign_vpn_live
from lib.http_connect import http_connect, parse_proxy


class ProbeOps:
    def probe(self, host: str, port: int = 443) -> int:
        """CONNECT probe via corporate Squid. Returns 0 on success."""
        self.reload_env()
        cfg = self.config()
        proxy = resolve_corporate_proxy(cfg)
        ph, pport = parse_proxy(proxy)
        self.log(f"proxy={ph}:{pport}")
        self.log(f"target={host}:{port}")
        try:
            sock, status = http_connect(ph, pport, host, port, timeout=5, nodelay=False)
            sock.close()
        except OSError as exc:
            status = str(exc)
            self.log(status)
            self.log("FAIL: Squid denied/failed CONNECT")
            return 1
        self.log(status)
        self.log("OK: Squid allows CONNECT to this host:port")
        return 0


    def _probe_exit(self, socks_port: int, delay: float = 0.0) -> None:
        """SOCKS5 CONNECT after TUN routes settle — first-second OK is a false green."""
        try:
            self._probe_exit_body(socks_port, delay)
        finally:
            snap = self.session.snapshot
            tun_pending = bool(snap.pending_win_tun)
            probe_ok = not snap.exit_probe_error
            if tun_pending and not probe_ok:
                self.log("TUN ещё открывается — kill switch не закрываю, watchdog ждёт")
            else:
                self.session.update(hold_watchdog=False)
            if (
                not probe_ok
                and get_kill_switch()
                and not tun_pending
            ):
                self._seal_on_dead_exit()
            if snap.want_watchdog and not tun_pending:
                try:
                    self.ensure_watchdog_daemon()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: {exc}")


    def _probe_exit_body(self, socks_port: int, delay: float = 0.0) -> None:
        try:
            from desktop.watchdog import (
                exit_probe_target,
                socks_https_probe,
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"проверка выхода: не удалось импортировать probe ({exc})")
            return
        if delay > 0:
            time.sleep(delay)
        if not self.singbox.running():
            err = self.session.snapshot.exit_probe_error or "sing-box stopped"
            self.session.update(exit_probe_error=err)
            return
        office = False
        home_udp = False
        probe_host, probe_path = "github.com", "/"
        try:
            cfg_now = self.config()
            office = bool(resolve_corporate_proxy(cfg_now))
            probe_host, probe_path = exit_probe_target(office=office)
            tr_now = require_transport(cfg_now)
            dial_now = choose_dial(tr_now, office=office)
            home_udp = dial_now == "amneziawg"
        except Exception:  # noqa: BLE001
            pass
        # AWG UDP dies if TUN /1 and KS land before the handshake.
        if not home_udp:
            self._claim_default_route()
        self.log(f"проверка выхода через SOCKS :{socks_port} → {probe_host}:443…")
        attempts = 3 if home_udp else 1
        err: str | None = None
        for attempt in range(attempts):
            if not self.singbox.running():
                err = self.session.snapshot.exit_probe_error or "sing-box stopped"
                self.session.update(exit_probe_error=err)
                return
            if attempt:
                self.log(f"проверка выхода: повтор {attempt + 1}/{attempts}")
                time.sleep(0.6)
            err = socks_https_probe(
                socks_port,
                host=probe_host,
                sni=probe_host,
                path=probe_path,
                timeout=12.0,
            )
            if not err:
                break
        if not self.singbox.running():
            err = self.session.snapshot.exit_probe_error or "sing-box stopped"
            self.session.update(exit_probe_error=err)
            return
        if err:
            self._diagnose_failed_exit(socks_port, err)
            return
        tail = "\n".join(self.singbox.tail_log(40)).lower()
        if "i/o timeout" in tail or "deadline exceeded" in tail:
            self.session.update(exit_probe_error="dns-timeout")
            self.log(
                "проверка выхода: HTTPS прошёл, но DNS/VLESS сыплет timeout — "
                "сайты могут не открываться (см. журнал sing-box)"
            )
            self._log_singbox_tail("после частичного OK")
            return
        self._on_exit_probe_ok(probe_host)

    def _diagnose_failed_exit(self, socks_port: int, err: str) -> None:
        from desktop.watchdog import exit_probe_target, https_probe_is_flake, socks_probe

        connect_err = socks_probe(socks_port, timeout=6.0)
        if connect_err:
            self.session.update(exit_probe_error=connect_err)
            self.log(f"проверка выхода: НЕ ОК — {connect_err}")
        elif https_probe_is_flake(err):
            # CONNECT through VLESS works. Cloudflare TLS often stalls via Squid;
            # tearing the tunnel down here is what "VPN disconnected itself".
            office = False
            try:
                office = bool(resolve_corporate_proxy(self.config()))
            except Exception:  # noqa: BLE001
                office = False
            host, _path = exit_probe_target(office=office)
            self.log(
                f"проверка выхода: CONNECT есть, HTTPS медленный ({err}) — туннель не рву"
            )
            self._on_exit_probe_ok(host)
            return
        else:
            self.session.update(exit_probe_error=err)
            self.log(f"проверка выхода: НЕ ОК — CONNECT есть, HTTPS нет ({err})")
        tail = "\n".join(self.singbox.tail_log(40)).lower()
        if "no recent network activity" in tail:
            self._log_udp_timeout_hint()
        else:
            self._log_reality_dpi_hint()
        self._log_singbox_tail("после неудачной проверки")

    def _log_udp_timeout_hint(self) -> None:
        self.session.update(exit_probe_hint="udp-timeout")
        office_now = False
        try:
            tr_now = require_transport(self.config())
            office_now = bool(resolve_corporate_proxy(self.config()))
            dial_now = choose_dial(tr_now, office=office_now)
            awg_now = amneziawg_opts(tr_now)
            udp_port = int((awg_now or {}).get("port") or 0) if dial_now == "amneziawg" else 0
        except Exception:  # noqa: BLE001
            dial_now, udp_port = "amneziawg", 0
        proto = "AmneziaWG" if dial_now == "amneziawg" else "VLESS+Reality"
        if udp_port and udp_port != 443:
            live = foreign_vpn_live()
            if live:
                self.log(
                    f"{proto} UDP :{udp_port} не дошёл до VPS. "
                    "Чужой туннель поднят ("
                    + ", ".join(live)
                    + ") — его split default перехватывает UDP."
                )
            elif office_now and dial_now == "amneziawg":
                self.log(
                    f"{proto} UDP :{udp_port} не ушёл с underlay. "
                    "Handshake не дошёл до VPS: bind должен смотреть на VPS, "
                    "не на Squid; проверьте маршрут /32 и резолв адреса сервера."
                )
            else:
                self.log(
                    f"{proto} UDP :{udp_port} не дошёл до VPS. "
                    "Проверьте, что на VPS слушает UDP и порт открыт в панели хостинга."
                )
        else:
            self.log(
                f"{proto} не дошёл до VPS (UDP timeout). "
                "UDP :443 часто режет домашний DPI"
            )

    def _log_reality_dpi_hint(self) -> None:
        try:
            cfg = self.config()
            office = bool(resolve_corporate_proxy(cfg))
            tr = require_transport(cfg)
            dial_now = choose_dial(tr, office=office)
            awg = amneziawg_opts(tr)
        except Exception:  # noqa: BLE001
            office, awg, dial_now = False, None, "vless-reality"
        if not office and not awg and dial_now == "vless-reality":
            self.session.update(exit_probe_hint="need-awg")
            self.log(
                "домашний DPI съел Reality: TCP до VPS живой, "
                "внутри туннеля — тишина. Без AmneziaWG дома интернет "
                "не заработает. На VPS: bash modes/vps/enable_amneziawg.sh"
            )

    def _claim_default_route(self) -> None:
        """Steal default now so apps do not leak while the HTTPS probe runs."""
        if not self.session.snapshot.pending_win_tun:
            return
        allow = list(self.session.snapshot.pending_allow)
        self.log("ставлю TUN split default сразу — трафик не ждёт проверку выхода")
        try:
            installed = self._install_win_tun_routes(allow, strict=False)
        except Exception as exc:  # noqa: BLE001
            self.log(f"TUN split: {exc}")
            return
        if not installed:
            return
        self.session.update(pending_win_tun=False)
        if self.session.snapshot.defer_win_ks and get_kill_switch():
            try:
                apply_kill_switch(
                    allow,
                    var_dir=self.paths.var_dir,
                    log=self.log,
                    blackhole=False,
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"kill switch: {exc}")
            self.session.update(defer_win_ks=False)

    def _on_exit_probe_ok(self, probe_host: str) -> None:
        self.session.update(
            exit_probe_error="",
            exit_probe_hint="",
            fail_closed=False,
        )
        self.log(f"проверка выхода: OK (HTTPS {probe_host} через SOCKS)")
        if self.session.snapshot.pending_win_tun:
            allow = list(self.session.snapshot.pending_allow)
            self.log("выход живой — ставлю TUN split default")
            if self._install_win_tun_routes(allow):
                self.session.update(pending_win_tun=False, tun_ready=True)
            if self.session.snapshot.defer_win_ks:
                apply_kill_switch(
                    allow,
                    var_dir=self.paths.var_dir,
                    log=self.log,
                    blackhole=False,
                )
                self.session.update(defer_win_ks=False)
        self._check_tun_owns_default()
        try:
            cfg = self.config()
            self.set_git_singbox(cfg, get_http_bridge_port())
        except Exception as exc:  # noqa: BLE001
            self.log(f"PAC/git после проверки: {exc}")
        try:
            self._maybe_start_reverse_ssh()
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh после проверки: {exc}")


    def _check_tun_owns_default(self) -> None:
        """Loopback /1 next to TUN /1 blackholes the browser. TUN must win."""
        from desktop.kill_switch import lift_ipv4_blackholes
        from desktop.tun import reclaim_tun_default, split_row_is_loopback, tun_owns_default, tun_split_rows

        rows = tun_split_rows()
        loop = [r for r in rows if split_row_is_loopback(r)]
        if loop:
            self.log("чёрные /1 на loopback мешают TUN — снимаю, чтобы браузер шёл в VPN")
            lift_ipv4_blackholes(log=self.log)
            rows = tun_split_rows()
        if tun_owns_default(rows):
            self.log("TUN владеет default: " + " | ".join(rows[:2]))
            return
        if procutil.is_admin():
            self.log("TUN auto_route не поставил /1 — ставлю сам")
            reclaim_tun_default()
            rows = tun_split_rows()
            if tun_owns_default(rows):
                self.log("TUN владеет default: " + " | ".join(rows[:2]))
                return
        self.log("TUN не владеет 0.0.0.0/1 — браузер пойдёт мимо VPN")


    def _seal_on_dead_exit(self) -> None:
        """Fail-closed: no working exit → block underlay except the VPS."""
        if self.session.snapshot.exit_probe_error == "dns-timeout":
            return
        try:
            from desktop.watchdog import socks_port_from_client, socks_probe

            if socks_probe(socks_port_from_client(self), timeout=4.0) is None:
                self.log(
                    "kill switch: SOCKS CONNECT жив — чёрные /1 не ставлю "
                    "(TUN ещё открывается или HTTPS тупил)"
                )
                self.session.update(exit_probe_error="")
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            cfg = self.config()
        except Exception as exc:  # noqa: BLE001
            self.log(f"kill switch: нет конфига ({exc})")
            return
        allow = list(self.session.snapshot.pending_allow)
        if not allow:
            allow = self._kill_switch_hosts(cfg)
        self.session.update(fail_closed=True, defer_win_ks=False)
        if self.session.snapshot.pending_win_tun and procutil.is_admin() and allow:
            try:
                self.log("выхода нет — трафик в TUN, чтобы не шёл мимо")
                self._install_win_tun_routes(allow, strict=False)
            except Exception as exc:  # noqa: BLE001
                self.log(f"TUN split при обрыве: {exc}")
            self.session.update(pending_win_tun=False)
        if kill_switch_is_sealed():
            self.log("kill switch: уже стоит — интернет закрыт, пока нет выхода")
            return
        self.log("kill switch: выхода нет — закрываю интернет кроме VPS")
        try:
            apply_kill_switch(
                allow, var_dir=self.paths.var_dir, log=self.log, blackhole=True
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"kill switch при обрыве: {exc}")

