"""Exit / Squid probes for OpsClient."""

from __future__ import annotations

import time
from typing import Any

from desktop import procutil
from desktop.config_io import (
    get_http_bridge_port,
    get_kill_switch,
    resolve_corporate_proxy,
)
from desktop.kill_switch import apply as apply_kill_switch
from desktop.kill_switch import is_applied as kill_switch_is_applied
from desktop.singbox_mode import (
    amneziawg_opts,
    choose_dial,
    hysteria2_opts,
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
            self._hold_watchdog = False
            probe_ok = not getattr(self, "_exit_probe_error", None)
            if not probe_ok and get_kill_switch():
                self._seal_on_dead_exit()
            if getattr(self, "_want_watchdog", False):
                try:
                    self.ensure_watchdog_daemon()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"watchdog: {exc}")


    def _probe_exit_body(self, socks_port: int, delay: float = 0.0) -> None:
        try:
            from desktop.watchdog import socks_https_probe, socks_probe
        except Exception as exc:  # noqa: BLE001
            self.log(f"проверка выхода: не удалось импортировать probe ({exc})")
            return
        if delay > 0:
            time.sleep(delay)
        if not self.singbox.running():
            self._exit_probe_error = self._exit_probe_error or "sing-box stopped"
            return
        self.log(f"проверка выхода через SOCKS :{socks_port} → 1.1.1.1:443…")
        home_udp = bool(getattr(self, "_defer_win_tun", False))
        try:
            cfg_now = self.config()
            office = bool(resolve_corporate_proxy(cfg_now))
            tr_now = require_transport(cfg_now)
            dial_now = choose_dial(tr_now, office=office)
            home_udp = home_udp or (dial_now in ("hysteria2", "amneziawg") and not office)
        except Exception:  # noqa: BLE001
            pass
        attempts = 3 if home_udp else 1
        err: str | None = None
        for attempt in range(attempts):
            if not self.singbox.running():
                self._exit_probe_error = self._exit_probe_error or "sing-box stopped"
                return
            if attempt:
                self.log(f"проверка выхода: повтор {attempt + 1}/{attempts}")
                time.sleep(0.6)
            err = socks_https_probe(socks_port, timeout=12.0)
            if not err:
                break
        if not self.singbox.running():
            self._exit_probe_error = self._exit_probe_error or "sing-box stopped"
            return
        if err:
            connect_err = socks_probe(socks_port, timeout=6.0)
            self._exit_probe_error = connect_err or err
            if connect_err:
                self.log(f"проверка выхода: НЕ ОК — {connect_err}")
            else:
                self.log(
                    f"проверка выхода: НЕ ОК — CONNECT есть, HTTPS нет ({err})"
                )
            tail = "\n".join(self.singbox.tail_log(40)).lower()
            if "no recent network activity" in tail:
                self._exit_probe_hint = "hy2-udp"
                try:
                    tr_now = require_transport(self.config())
                    office_now = bool(resolve_corporate_proxy(self.config()))
                    dial_now = choose_dial(tr_now, office=office_now)
                    awg_now = amneziawg_opts(tr_now)
                    hy_now = hysteria2_opts(tr_now)
                    udp_port = int(
                        (awg_now or {}).get("port")
                        or (hy_now or {}).get("port")
                        or 0
                    )
                except Exception:  # noqa: BLE001
                    dial_now, udp_port = "hysteria2", 0
                proto = "AmneziaWG" if dial_now == "amneziawg" else "Hysteria2"
                if udp_port and udp_port != 443:
                    live = foreign_vpn_live()
                    if live:
                        self.log(
                            f"{proto} UDP :{udp_port} не дошёл до VPS. "
                            "Чужой туннель поднят ("
                            + ", ".join(live)
                            + ") — его split default перехватывает UDP."
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
            else:
                try:
                    cfg = self.config()
                    office = bool(resolve_corporate_proxy(cfg))
                    tr = require_transport(cfg)
                    dial_now = choose_dial(tr, office=office)
                    hy = hysteria2_opts(tr)
                    awg = amneziawg_opts(tr)
                except Exception:  # noqa: BLE001
                    office, hy, awg, dial_now = False, None, None, "vless-reality"
                if not office and not hy and not awg and dial_now == "vless-reality":
                    self._exit_probe_hint = "need-hy2"
                    self.log(
                        "домашний DPI съел Reality: TCP до VPS живой, "
                        "внутри туннеля — тишина. Без Hysteria2 или AmneziaWG дома интернет "
                        "не заработает. На VPS: bash modes/vps/enable_hysteria2.sh "
                        "или bash modes/vps/enable_amneziawg.sh"
                    )
            self._log_singbox_tail("после неудачной проверки")
            return
        tail = "\n".join(self.singbox.tail_log(40)).lower()
        if "i/o timeout" in tail or "deadline exceeded" in tail:
            self._exit_probe_error = "dns-timeout"
            self.log(
                "проверка выхода: HTTPS прошёл, но DNS/VLESS сыплет timeout — "
                "сайты могут не открываться (см. журнал sing-box)"
            )
            self._log_singbox_tail("после частичного OK")
            return
        self._exit_probe_error = None
        self._exit_probe_hint = None
        self._fail_closed = False
        self.log("проверка выхода: OK (HTTPS через SOCKS)")
        if getattr(self, "_defer_win_tun", False):
            self._defer_win_tun = False
            self._bring_up_win_tun()
        elif getattr(self, "_pending_win_tun", False):
            self._pending_win_tun = False
            allow = list(getattr(self, "_pending_allow", []) or [])
            self.log("выход живой — ставлю TUN split default")
            self._install_win_tun_routes(allow)
            if getattr(self, "_defer_win_ks", False):
                apply_kill_switch(
                    allow, var_dir=self.paths.var_dir, log=self.log
                )
                self._defer_win_ks = False
        try:
            cfg = self.config()
            self.set_git_singbox(cfg, get_http_bridge_port())
        except Exception as exc:  # noqa: BLE001
            self.log(f"PAC/git после проверки: {exc}")
        try:
            self._maybe_start_reverse_ssh()
        except Exception as exc:  # noqa: BLE001
            self.log(f"reverse-ssh после проверки: {exc}")


    def _seal_on_dead_exit(self) -> None:
        """Fail-closed: no working exit → block underlay except the VPS."""
        if getattr(self, "_exit_probe_error", None) == "dns-timeout":
            return
        try:
            cfg = self.config()
        except Exception as exc:  # noqa: BLE001
            self.log(f"kill switch: нет конфига ({exc})")
            return
        allow = list(getattr(self, "_pending_allow", None) or [])
        if not allow:
            allow = self._kill_switch_hosts(cfg)
        self._fail_closed = True
        self._defer_win_ks = False
        if getattr(self, "_pending_win_tun", False) and procutil.is_admin() and allow:
            try:
                self.log("выхода нет — трафик в TUN, чтобы не шёл мимо")
                self._install_win_tun_routes(allow)
            except Exception as exc:  # noqa: BLE001
                self.log(f"TUN split при обрыве: {exc}")
            self._pending_win_tun = False
        if kill_switch_is_applied():
            self.log("kill switch: уже стоит — интернет закрыт, пока нет выхода")
            return
        self.log("kill switch: выхода нет — закрываю интернет кроме VPS")
        try:
            apply_kill_switch(allow, var_dir=self.paths.var_dir, log=self.log)
        except Exception as exc:  # noqa: BLE001
            self.log(f"kill switch при обрыве: {exc}")

