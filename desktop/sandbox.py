"""Isolated protocol checks: no TUN/KS, traffic via underlay even if a VPN is on."""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from desktop import procutil
from desktop.client import OpsClient
from desktop.config_io import get_server, get_server_host, get_sing_box_path
from desktop.kill_switch import underlay_gateway
from desktop.singbox_mode import (
    amneziawg_opts,
    awg_endpoint,
    hy2_outbound,
    hysteria2_opts,
    require_transport,
    _dns_v12,
)
from desktop.tun import (
    bind_underlay_socket,
    default_route_lines,
    foreign_vpn_live,
    foreign_vpn_processes,
    leftover_vpn_ifaces,
    underlay_bind_info,
)
from desktop.watchdog import socks_https_probe, socks_probe

LogFn = Callable[[str], None]

SANDBOX_SOCKS = 18080
SANDBOX_HTTP = 18088
VLESS_SOCKS = 18180
VLESS_HTTP = 18188
AWG_SOCKS = 18280
AWG_HTTP = 18288


def _noop(msg: str) -> None:
    pass


def _tcp(
    host: str,
    port: int,
    timeout: float = 5.0,
    *,
    bind_ip: str = "",
    if_index: int = 0,
) -> tuple[bool, str, str]:
    t0 = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bind_underlay_socket(sock, ip=bind_ip, if_index=if_index)
        sock.settimeout(timeout)
        sock.connect((host, port))
        peer = sock.getpeername()
        local = sock.getsockname()[0]
        sock.close()
        sock = None
        ms = int((time.monotonic() - t0) * 1000)
        return True, f"{peer[0]}:{peer[1]} {ms}ms via {local}", local
    except OSError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return False, f"{exc} {ms}ms", bind_ip
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _tls(
    host: str,
    port: int,
    sni: str,
    *,
    bind_ip: str = "",
    if_index: int = 0,
) -> tuple[bool, str]:
    """TLS to VPS with Reality SNI — python, not sing-box, via underlay."""
    t0 = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bind_underlay_socket(sock, ip=bind_ip, if_index=if_index)
        sock.settimeout(6)
        sock.connect((host, port))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(sock, server_hostname=sni)
        sock = None
        ver = tls.version() or "tls"
        tls.close()
        ms = int((time.monotonic() - t0) * 1000)
        return True, f"{ver} sni={sni} {ms}ms"
    except OSError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return False, f"{exc} {ms}ms"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _http(
    host: str,
    port: int,
    *,
    bind_ip: str = "",
    if_index: int = 0,
) -> tuple[bool, str]:
    """Plain GET — Reality forwards unknown traffic to handshake dest."""
    t0 = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bind_underlay_socket(sock, ip=bind_ip, if_index=if_index)
        sock.settimeout(6)
        sock.connect((host, port))
        sock.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
        data = sock.recv(120)
        ms = int((time.monotonic() - t0) * 1000)
        if data:
            preview = data.split(b"\r\n", 1)[0][:60].decode("ascii", "replace")
            return True, f"{preview} {ms}ms"
        return False, f"пусто {ms}ms"
    except OSError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return False, f"{exc} {ms}ms"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _udp(
    host: str,
    port: int,
    *,
    bind_ip: str = "",
    if_index: int = 0,
) -> tuple[bool, str]:
    """Raw UDP from underlay — ICMP/unreachable vs silent drop."""
    t0 = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bind_underlay_socket(sock, ip=bind_ip, if_index=if_index)
        sock.settimeout(3)
        sock.sendto(b"\x00" * 32, (host, int(port)))
        try:
            data, addr = sock.recvfrom(512)
            ms = int((time.monotonic() - t0) * 1000)
            src = addr[0] if addr else "?"
            return True, f"ответ {len(data)}b {src} {ms}ms"
        except TimeoutError:
            ms = int((time.monotonic() - t0) * 1000)
            return True, f"датаграмма ушла, ответа нет {ms}ms"
        except OSError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return False, f"{exc} {ms}ms"
    except OSError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return False, f"{exc} {ms}ms"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _line(ok: bool, name: str, detail: str) -> str:
    mark = "OK  " if ok else "FAIL"
    return f"  {mark}  {name:<24} {detail}"


def _nodelay(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


class _TcpRelay:
    """Python splice 127.0.0.1 → VPS via underlay (VPN TUN is skipped)."""

    def __init__(
        self,
        dest: str,
        port: int,
        *,
        bind_ip: str = "",
        if_index: int = 0,
    ) -> None:
        self.dest = dest
        self.dport = port
        self.bind_ip = bind_ip
        self.if_index = if_index
        self.accepted = 0
        self.upstream_ok = 0
        self.upstream_err = ""
        self.bytes_up = 0
        self.bytes_down = 0
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(32)
        self._srv.settimeout(0.4)
        self.port = int(self._srv.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, name="sandbox-relay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self.accepted += 1
            threading.Thread(target=self._session, args=(client,), daemon=True).start()

    def _session(self, client: socket.socket) -> None:
        _nodelay(client)
        up: socket.socket | None = None
        try:
            up = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _nodelay(up)
            bind_underlay_socket(up, ip=self.bind_ip, if_index=self.if_index)
            up.settimeout(8)
            up.connect((self.dest, self.dport))
            up.settimeout(None)
        except OSError as exc:
            self.upstream_err = str(exc)
            try:
                client.close()
            except OSError:
                pass
            if up is not None:
                try:
                    up.close()
                except OSError:
                    pass
            return
        self.upstream_ok += 1

        def pump(src: socket.socket, dst: socket.socket, *, to_vps: bool) -> None:
            try:
                while True:
                    data = src.recv(16384)
                    if not data:
                        break
                    dst.sendall(data)
                    if to_vps:
                        self.bytes_up += len(data)
                    else:
                        self.bytes_down += len(data)
            except OSError:
                pass
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        a = threading.Thread(target=pump, args=(client, up), kwargs={"to_vps": True}, daemon=True)
        b = threading.Thread(target=pump, args=(up, client), kwargs={"to_vps": False}, daemon=True)
        a.start()
        b.start()
        a.join()
        b.join()
        for s in (client, up):
            try:
                s.close()
            except OSError:
                pass


def _route_block(bind_iface: str) -> dict:
    return {
        "auto_detect_interface": False,
        **({"default_interface": bind_iface} if bind_iface else {}),
        "final": "proxy",
    }


def _vless_cfg(
    transport: dict,
    *,
    server: str,
    port: int,
    log_path: Path,
    vision: bool = True,
    bind_iface: str = "",
    socks_port: int = SANDBOX_SOCKS,
    http_port: int = SANDBOX_HTTP,
) -> dict:
    outbound: dict = {
        "type": "vless",
        "tag": "proxy",
        "server": server,
        "server_port": int(port),
        "uuid": transport["uuid"],
        **({"flow": "xtls-rprx-vision"} if vision else {}),
        "packet_encoding": "xudp",
        "tls": {
            "enabled": True,
            "server_name": transport["server_name"],
            "utls": {"enabled": True, "fingerprint": "chrome"},
            "reality": {
                "enabled": True,
                "public_key": transport["public_key"],
                "short_id": transport["short_id"],
            },
        },
    }
    if bind_iface and server not in ("127.0.0.1", "localhost"):
        outbound["bind_interface"] = bind_iface
    return {
        "log": {
            "level": "info",
            "timestamp": True,
            "output": str(log_path).replace("\\", "/"),
        },
        "dns": {
            "servers": [
                {
                    "tag": "dns-proxy",
                    "address": "https://1.1.1.1/dns-query",
                    "detour": "proxy",
                }
            ],
            "final": "dns-proxy",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": int(socks_port),
            },
            {
                "type": "http",
                "tag": "http-in",
                "listen": "127.0.0.1",
                "listen_port": int(http_port),
            },
        ],
        "outbounds": [
            outbound,
            {
                "type": "direct",
                "tag": "direct",
                **({"bind_interface": bind_iface} if bind_iface else {}),
            },
        ],
        "route": _route_block(bind_iface),
    }


def _hy2_cfg(
    transport: dict,
    *,
    server: str,
    port: int,
    password: str,
    sni: str,
    log_path: Path,
    bind_iface: str = "",
    socks_port: int = SANDBOX_SOCKS,
    http_port: int = SANDBOX_HTTP,
    obfs_password: str = "",
) -> dict:
    hy = {
        "port": port,
        "password": password,
        "server_name": sni,
        "insecure": True,
        "obfs_password": obfs_password,
    }
    outbound = hy2_outbound(server, hy, bind_iface=bind_iface)
    return {
        "log": {
            "level": "info",
            "timestamp": True,
            "output": str(log_path).replace("\\", "/"),
        },
        "dns": {
            "servers": [
                {
                    "tag": "dns-proxy",
                    "address": "https://1.1.1.1/dns-query",
                    "detour": "proxy",
                }
            ],
            "final": "dns-proxy",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": int(socks_port),
            },
            {
                "type": "http",
                "tag": "http-in",
                "listen": "127.0.0.1",
                "listen_port": int(http_port),
            },
        ],
        "outbounds": [
            outbound,
            {
                "type": "direct",
                "tag": "direct",
                **({"bind_interface": bind_iface} if bind_iface else {}),
            },
        ],
        "route": _route_block(bind_iface),
    }


def _awg_cfg(
    opts: dict,
    *,
    server: str,
    log_path: Path,
    bind_iface: str = "",
    socks_port: int = AWG_SOCKS,
    http_port: int = AWG_HTTP,
) -> dict:
    return {
        "log": {
            "level": "info",
            "timestamp": True,
            "output": str(log_path).replace("\\", "/"),
        },
        "dns": _dns_v12(),
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": int(socks_port),
            },
            {
                "type": "http",
                "tag": "http-in",
                "listen": "127.0.0.1",
                "listen_port": int(http_port),
            },
        ],
        "endpoints": [awg_endpoint(server, opts, bind_iface=bind_iface)],
        "outbounds": [
            {
                "type": "direct",
                "tag": "direct",
                **({"bind_interface": bind_iface} if bind_iface else {}),
            }
        ],
        "route": {
            **_route_block(bind_iface),
            "default_domain_resolver": "dns-local",
        },
    }


def _start_box(exe: Path, cfg_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [str(exe), "run", "-c", str(cfg_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=procutil.creationflags(),
    )


def _stop_box(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.pid is None:
        return
    procutil.kill_pids([proc.pid])
    try:
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        pass


def _probes(
    report: Callable[[bool, str, str], None],
    *,
    timeout: float,
    socks_port: int = SANDBOX_SOCKS,
) -> None:
    err = socks_probe(socks_port, timeout=min(8.0, timeout))
    report(not err, "SOCKS CONNECT :443", err or "1.1.1.1:443")
    err = socks_https_probe(socks_port, timeout=timeout)
    report(not err, "HTTPS 1.1.1.1/trace", err or "cdn-cgi/trace 200")
    err = socks_https_probe(
        socks_port,
        host="example.com",
        sni="example.com",
        path="/",
        timeout=timeout,
    )
    report(not err, "HTTPS example.com", err or "VLESS+DNS")
    err = socks_https_probe(
        socks_port,
        path="/dns-query?name=example.com&type=A",
        timeout=timeout,
    )
    report(not err, "DoH 1.1.1.1", err or "dns-query :443")


def _hy2_probes(
    report: Callable[[bool, str, str], None],
    *,
    timeout: float,
    socks_port: int = SANDBOX_SOCKS,
) -> tuple[bool, bool]:
    conn_err = socks_probe(socks_port, timeout=min(8.0, timeout))
    report(not conn_err, "hy2 CONNECT :443", conn_err or "1.1.1.1:443")
    https_err = socks_https_probe(socks_port, timeout=timeout)
    report(not https_err, "hy2 HTTPS", https_err or "cdn-cgi/trace 200")
    return (not conn_err), (not https_err)


def _dump_tail(log_path: Path, log: LogFn) -> None:
    tail = _tail(log_path)
    if not tail:
        return
    log("  журнал sandbox sing-box:")
    for ln in tail:
        log(f"    {ln}")


def run_sandbox(client: OpsClient, *, log: LogFn = _noop) -> int:
    """Protocol checks via underlay NIC. Does not touch TUN, KS, or Amnezia."""
    cfg = client.config()
    host = get_server_host(cfg)
    transport = require_transport(cfg)
    port = int(transport.get("port") or get_server(cfg).get("port") or 443)
    failed = 0

    def report(ok: bool, name: str, detail: str) -> None:
        nonlocal failed
        if not ok:
            failed += 1
        log(_line(ok, name, detail))

    def note(ok: bool, name: str, detail: str) -> None:
        log(_line(ok, name, detail))

    leftover = leftover_vpn_ifaces()
    procs = foreign_vpn_processes()
    live = foreign_vpn_live()
    sni = str(transport.get("server_name") or "www.cloudflare.com")
    log("песочница: трафик с Ethernet/Wi-Fi, TUN/Amnezia не трогаем, порты :18080/:18088")
    if leftover:
        note(
            True,
            "чужой туннель",
            ", ".join(n for _i, n in leftover) + " поднят (адаптер с IP), маршруты не снимаем",
        )
    elif procs:
        note(
            True,
            "чужой VPN",
            ", ".join(procs) + " установлен, туннель не поднят — на проверку не влияет",
        )
    else:
        note(True, "чужой VPN", "нет")
    for row in default_route_lines():
        note(True, "default 0.0.0.0/0", row)

    bind_iface, bind_ip, if_index = underlay_bind_info(host)
    gw = underlay_gateway(host) or ""
    note(bool(bind_iface), "underlay NIC", bind_iface or "не определена")
    if bind_ip:
        note(True, "underlay IP", f"{bind_ip} if={if_index or '—'}")
    if gw:
        note(True, "gateway", gw)

    ok, detail, local_ip = _tcp(host, port, bind_ip=bind_ip, if_index=if_index)
    if not bind_ip and local_ip:
        bind_ip = local_ip
    report(ok, "1 TCP python → VPS", detail)
    if not ok:
        log("  слой 1 мёртв: до VPS нет даже обычного TCP с python (мимо VPN)")
        return 1

    ref_ok, ref_detail = _tls(sni, 443, sni, bind_ip=bind_ip, if_index=if_index)
    note(ref_ok, f"2 TLS контроль → {sni}", ref_detail)
    http_ok, http_detail = _http(host, port, bind_ip=bind_ip, if_index=if_index)
    note(http_ok, "2 HTTP → VPS", http_detail)
    tls_ok, tls_detail = _tls(host, port, sni, bind_ip=bind_ip, if_index=if_index)
    report(tls_ok, "2 TLS → VPS", tls_detail)
    if not tls_ok and http_ok:
        log(
            "  сервер жив (HTTP отвечает), а TLS ClientHello с этой сети глотается. "
            "Обычный DPI домашнего провайдера на :443."
        )
    elif not tls_ok and ref_ok and not http_ok:
        log(
            "  слой 2 мёртв: TCP есть, данных нет. На VPS: "
            "systemctl status sing-box; journalctl -u sing-box -n 50"
        )
        return 1

    exe = client.singbox.find_sing_box(get_sing_box_path(cfg))
    if not exe:
        report(False, "sing-box", "не найден (download-sing-box)")
        return 1
    note(True, "sing-box", str(exe))

    log("— прямой интернет (python, мимо SOCKS/VPN)")
    net_ok, net_d, _ = _tcp("1.1.1.1", 443, bind_ip=bind_ip, if_index=if_index)
    report(net_ok, "интернет TCP 1.1.1.1", net_d)
    tls_cf, tls_d = _tls(
        "1.1.1.1", 443, "cloudflare-dns.com", bind_ip=bind_ip, if_index=if_index
    )
    report(tls_cf, "интернет TLS 1.1.1.1", tls_d)
    ex_ok, ex_d = _tls(
        "example.com", 443, "example.com", bind_ip=bind_ip, if_index=if_index
    )
    report(ex_ok, "интернет TLS example.com", ex_d)
    if not (net_ok or tls_cf or ex_ok):
        log(
            "  прямой интернет на 1.1.1.1/example.com закрыт (часто WFP/Amnezia). "
            "VPS TCP жив — протоколы всё равно проверяю."
        )

    client.paths.var_dir.mkdir(parents=True, exist_ok=True)
    client.paths.logs_dir.mkdir(parents=True, exist_ok=True)
    vless_cfg_path = client.paths.var_dir / "sandbox-vless.json"
    vless_log = client.paths.logs_dir / "sandbox-vless.log"
    hy2_cfg_path = client.paths.var_dir / "sandbox-hy2.json"
    hy2_log = client.paths.logs_dir / "sandbox-hy2.log"
    awg_cfg_path = client.paths.var_dir / "sandbox-awg.json"
    awg_log = client.paths.logs_dir / "sandbox-awg.log"

    hy = hysteria2_opts(transport)
    awg = amneziawg_opts(transport)
    if hy:
        udp_ok, udp_detail = _udp(
            host, int(hy["port"]), bind_ip=bind_ip, if_index=if_index
        )
        report(udp_ok, f"UDP python :{hy['port']}", udp_detail)
    if awg:
        udp_ok, udp_detail = _udp(
            host, int(awg["port"]), bind_ip=bind_ip, if_index=if_index
        )
        report(udp_ok, f"UDP python AWG :{awg['port']}", udp_detail)

    log("— VLESS+Reality, Hysteria2 и AmneziaWG параллельно (разные порты, bind underlay)")
    lock = threading.Lock()

    def locked_report(ok: bool, name: str, detail: str) -> None:
        with lock:
            report(ok, name, detail)

    def locked_note(ok: bool, name: str, detail: str) -> None:
        with lock:
            note(ok, name, detail)

    direct_ok = False
    hy2_ok = False
    awg_ok = False

    def run_vless() -> None:
        nonlocal direct_ok
        _write_box(
            vless_cfg_path,
            vless_log,
            _vless_cfg(
                transport,
                server=host,
                port=port,
                log_path=vless_log,
                bind_iface=bind_iface,
                socks_port=VLESS_SOCKS,
                http_port=VLESS_HTTP,
            ),
        )
        proc = _start_box(exe, vless_cfg_path)
        try:
            if not procutil.wait_port_open("127.0.0.1", VLESS_SOCKS, timeout=6.0):
                locked_note(False, "VLESS SOCKS", f"не открылся :{VLESS_SOCKS}")
                return
            conn_err = socks_probe(VLESS_SOCKS, timeout=8.0)
            https_err = socks_https_probe(VLESS_SOCKS, timeout=12.0)
            locked_report(not conn_err, "VLESS CONNECT", conn_err or "1.1.1.1:443")
            locked_report(not https_err, "VLESS HTTPS", https_err or "HTTPS через SOCKS")
            direct_ok = not https_err
        finally:
            _stop_box(proc)

    def run_hy2() -> None:
        nonlocal hy2_ok
        if not hy:
            locked_note(True, "Hysteria2", "пароль не задан — слой пропущен")
            return
        _write_box(
            hy2_cfg_path,
            hy2_log,
            _hy2_cfg(
                transport,
                server=host,
                port=int(hy["port"]),
                password=hy["password"],
                sni=str(hy["server_name"]),
                log_path=hy2_log,
                bind_iface=bind_iface,
                socks_port=SANDBOX_SOCKS,
                http_port=SANDBOX_HTTP,
                obfs_password=str(hy.get("obfs_password") or ""),
            ),
        )
        proc = _start_box(exe, hy2_cfg_path)
        try:
            if not procutil.wait_port_open("127.0.0.1", SANDBOX_SOCKS, timeout=6.0):
                locked_note(False, "hy2 SOCKS", f"не открылся :{SANDBOX_SOCKS}")
                return
            _conn, hy2_https = _hy2_probes(
                locked_report, timeout=12.0, socks_port=SANDBOX_SOCKS
            )
            hy2_ok = hy2_https
        finally:
            _stop_box(proc)

    def run_awg() -> None:
        nonlocal awg_ok
        if not awg:
            locked_note(True, "AmneziaWG", "ключи не заданы — слой пропущен")
            return
        awg_exe = client.singbox.find_awg_sing_box()
        if not awg_exe:
            locked_note(
                False,
                "AmneziaWG",
                "нет AWG-сборки (download-sing-box-awg) — слой пропущен",
            )
            return
        _write_box(
            awg_cfg_path,
            awg_log,
            _awg_cfg(
                awg,
                server=host,
                log_path=awg_log,
                bind_iface=bind_iface,
            ),
        )
        proc = _start_box(awg_exe, awg_cfg_path)
        try:
            if not procutil.wait_port_open("127.0.0.1", AWG_SOCKS, timeout=8.0):
                locked_note(False, "AWG SOCKS", f"не открылся :{AWG_SOCKS}")
                return
            conn_err = socks_probe(AWG_SOCKS, timeout=10.0)
            https_err = socks_https_probe(AWG_SOCKS, timeout=14.0)
            locked_report(not conn_err, "AWG CONNECT", conn_err or "1.1.1.1:443")
            locked_report(not https_err, "AWG HTTPS", https_err or "HTTPS через SOCKS")
            awg_ok = not https_err
        finally:
            _stop_box(proc)

    t_vless = threading.Thread(target=run_vless, name="sandbox-vless", daemon=True)
    t_hy2 = threading.Thread(target=run_hy2, name="sandbox-hy2", daemon=True)
    t_awg = threading.Thread(target=run_awg, name="sandbox-awg", daemon=True)
    t_vless.start()
    t_hy2.start()
    t_awg.start()
    t_vless.join()
    t_hy2.join()
    t_awg.join()

    if awg_ok:
        log("итог: AmneziaWG живой с underlay — Reality можно оставить офису")
        return 0
    if hy2_ok:
        log("итог: Hysteria2 живой с underlay — Reality можно оставить офису")
        return 0
    if hy:
        _dump_tail(hy2_log, log)
        if live:
            log(
                "  Hy2 с underlay не прошёл при поднятом чужом туннеле "
                f"({', '.join(live)}). Песочница маршруты не снимала."
            )
        else:
            log(
                "  Hysteria2 не дошёл до VPS (QUIC/HTTPS). "
                "Проверьте UDP на VPS, пароль, SNI и что порт открыт в панели хостинга."
            )
    if awg:
        _dump_tail(awg_log, log)
        log(
            "  AmneziaWG не дошёл до VPS. Проверьте UDP на VPS, ключи и "
            "bash modes/vps/enable_amneziawg.sh"
        )
    if not direct_ok:
        _dump_tail(vless_log, log)

    relay_ok: bool | None = None
    cfg_path = vless_cfg_path
    log_path = vless_log
    if not direct_ok and not hy2_ok and not awg_ok:
        relay_ok = False
        for vision in (True, False):
            label = "vision" if vision else "без flow"
            log(f"— VLESS через реле python ({label}), выход с underlay")
            relay = _TcpRelay(host, port, bind_ip=bind_ip, if_index=if_index)
            relay.start()
            _write_box(
                cfg_path,
                log_path,
                _vless_cfg(
                    transport,
                    server="127.0.0.1",
                    port=relay.port,
                    log_path=log_path,
                    vision=vision,
                    bind_iface="",
                ),
            )
            proc = _start_box(exe, cfg_path)
            round_fail = 0

            def round_report(ok: bool, name: str, detail: str) -> None:
                nonlocal round_fail
                if not ok:
                    round_fail += 1
                note(ok, name, detail)

            try:
                if not procutil.wait_port_open("127.0.0.1", SANDBOX_SOCKS, timeout=6.0):
                    note(False, "реле SOCKS", f"не открылся :{SANDBOX_SOCKS}")
                    round_fail += 1
                else:
                    note(
                        True,
                        "реле TCP",
                        f"127.0.0.1:{relay.port} → {host}:{port} src={bind_ip or 'auto'}",
                    )
                    _probes(round_report, timeout=12.0)
                    note(
                        True,
                        "реле байты",
                        f"↑{relay.bytes_up} ↓{relay.bytes_down} "
                        f"accept={relay.accepted} vps={relay.upstream_ok}",
                    )
                    if relay.upstream_err and not relay.upstream_ok:
                        note(False, "реле upstream", relay.upstream_err)
            finally:
                _stop_box(proc)
                relay.stop()
                time.sleep(0.3)
            if round_fail == 0:
                relay_ok = True
                break
            if vision:
                log("  vision через реле не прошёл — пробую без flow")
        if not relay_ok:
            failed += 1
    elif not hy:
        log("— полный набор через прямой VLESS")
        _write_box(
            cfg_path,
            log_path,
            _vless_cfg(
                transport,
                server=host,
                port=port,
                log_path=log_path,
                bind_iface=bind_iface,
            ),
        )
        proc = _start_box(exe, cfg_path)
        try:
            if procutil.wait_port_open("127.0.0.1", SANDBOX_SOCKS, timeout=6.0):
                _probes(report, timeout=12.0)
            else:
                report(False, "песочница SOCKS", f"не открылся :{SANDBOX_SOCKS}")
        finally:
            _stop_box(proc)

    log("песочница остановлена")
    if failed:
        _dump_tail(log_path, log)
    return _finish(
        failed, leftover, direct_ok, relay_ok, hy2_ok=hy2_ok, awg_ok=awg_ok, log=log
    )


def _write_box(cfg_path: Path, log_path: Path, box_cfg: dict) -> None:
    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass
    cfg_path.write_text(json.dumps(box_cfg, indent=2) + "\n", encoding="utf-8")


def _finish(
    failed: int,
    leftover: list,
    direct_ok: bool,
    relay_ok: bool | None,
    *,
    hy2_ok: bool,
    awg_ok: bool = False,
    log: LogFn,
) -> int:
    if awg_ok:
        log("итог: AmneziaWG с этой сети живой (трафик шёл мимо VPN TUN)")
        return 0
    if hy2_ok:
        log("итог: Hysteria2 с этой сети живой (трафик шёл мимо VPN TUN)")
        return 0
    if leftover and relay_ok and not direct_ok:
        log(
            "итог: протокол живой через python. sing-box до VPS напрямую не дошёл "
            "(чужой туннель фильтрует процесс). Песочница Amnezia не трогала."
        )
        return 0
    if leftover and not direct_ok and not relay_ok:
        log(
            "итог: TCP до VPS есть, Reality/Hy2/AWG с underlay не отвечает. "
            "Это не idle-служба: смотрите DPI, порт и пароль на VPS."
        )
        return 1
    if failed:
        log(f"итог: {failed} провал(а)")
        return 1
    log("итог: VLESS с этой сети живой (TUN в песочнице не проверяется)")
    return 0


def _tail(path: Path, n: int = 16) -> list[str]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in lines[-n:] if ln.strip()]
