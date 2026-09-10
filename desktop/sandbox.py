"""Isolated VLESS checks: no TUN, no kill switch, can run beside Amnezia."""

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
from desktop.singbox_mode import require_transport
from desktop.tun import (
    default_route_lines,
    detect_bind_interface,
    foreign_vpn_processes,
    leftover_vpn_ifaces,
)
from desktop.watchdog import socks_https_probe, socks_probe

LogFn = Callable[[str], None]

SANDBOX_SOCKS = 18080
SANDBOX_HTTP = 18088


def _noop(msg: str) -> None:
    pass


def _tcp(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str, str]:
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        peer = sock.getpeername()
        local = sock.getsockname()[0]
        sock.close()
        ms = int((time.monotonic() - t0) * 1000)
        return True, f"{peer[0]}:{peer[1]} {ms}ms via {local}", local
    except OSError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return False, f"{exc} {ms}ms", ""


def _tls(host: str, port: int, sni: str, *, bind_ip: str = "") -> tuple[bool, str]:
    """TLS to VPS with Reality SNI — python.exe, not sing-box."""
    t0 = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if bind_ip:
            sock.bind((bind_ip, 0))
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


def _http(host: str, port: int, *, bind_ip: str = "") -> tuple[bool, str]:
    """Plain GET — Reality forwards unknown traffic to handshake dest."""
    t0 = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if bind_ip:
            sock.bind((bind_ip, 0))
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


def _line(ok: bool, name: str, detail: str) -> str:
    mark = "OK  " if ok else "FAIL"
    return f"  {mark}  {name:<24} {detail}"


def _nodelay(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


class _TcpRelay:
    """Python splice 127.0.0.1 → VPS. Amnezia lets python.exe out; sing-box often not."""

    def __init__(self, dest: str, port: int, *, bind_ip: str = "") -> None:
        self.dest = dest
        self.dport = port
        self.bind_ip = bind_ip
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
            if self.bind_ip:
                up.bind((self.bind_ip, 0))
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


def _vless_cfg(
    transport: dict,
    *,
    server: str,
    port: int,
    log_path: Path,
    vision: bool = True,
) -> dict:
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
                "listen_port": SANDBOX_SOCKS,
            },
            {
                "type": "http",
                "tag": "http-in",
                "listen": "127.0.0.1",
                "listen_port": SANDBOX_HTTP,
            },
        ],
        "outbounds": [
            {
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
            },
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "auto_detect_interface": False,
            "final": "proxy",
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


def _probes(report: Callable[[bool, str, str], None], *, timeout: float) -> None:
    err = socks_probe(SANDBOX_SOCKS, timeout=min(8.0, timeout))
    report(not err, "SOCKS CONNECT :443", err or "1.1.1.1:443")
    err = socks_https_probe(SANDBOX_SOCKS, timeout=timeout)
    report(not err, "HTTPS 1.1.1.1/trace", err or "cdn-cgi/trace 200")
    err = socks_https_probe(
        SANDBOX_SOCKS,
        host="example.com",
        sni="example.com",
        path="/",
        timeout=timeout,
    )
    report(not err, "HTTPS example.com", err or "VLESS+DNS")
    err = socks_https_probe(
        SANDBOX_SOCKS,
        path="/dns-query?name=example.com&type=A",
        timeout=timeout,
    )
    report(not err, "DoH 1.1.1.1", err or "dns-query :443")


def run_sandbox(client: OpsClient, *, log: LogFn = _noop) -> int:
    """VLESS over SOCKS. Beside Amnezia: TCP relay in python.exe, no TUN."""
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
    others = [name for _idx, name in leftover]
    active = bool(leftover)
    sni = str(transport.get("server_name") or "www.cloudflare.com")
    log("песочница: слои отдельно, без TUN/kill switch, Amnezia не трогаем")
    if leftover:
        note(
            True,
            "чужой туннель",
            ", ".join(n for _i, n in leftover) + " поднят (адаптер с IP)",
        )
    elif procs:
        note(True, "чужой VPN", ", ".join(procs) + " запущен, но туннель опущен")
    else:
        note(True, "чужой VPN", "нет")
    for row in default_route_lines():
        note(True, "default 0.0.0.0/0", row)

    bind = detect_bind_interface(host) or ""
    gw = underlay_gateway(host) or ""
    note(bool(bind), "underlay NIC", bind or "не определена")
    if gw:
        note(True, "gateway", gw)

    ok, detail, local_ip = _tcp(host, port)
    report(ok, "1 TCP python → VPS", detail)
    if not ok:
        log("  слой 1 мёртв: до VPS нет даже обычного TCP с python")
        return 1

    ref_ok, ref_detail = _tls(sni, 443, sni, bind_ip=None)
    note(ref_ok, f"2 TLS контроль → {sni}", ref_detail)
    http_ok, http_detail = _http(host, port, bind_ip=local_ip)
    note(http_ok, "2 HTTP → VPS", http_detail)
    tls_ok, tls_detail = _tls(host, port, sni, bind_ip=local_ip)
    report(tls_ok, "2 TLS → VPS", tls_detail)
    if not tls_ok and http_ok:
        log(
            "  сервер жив (HTTP отвечает), а TLS ClientHello с этой сети глотается. "
            "Обычный DPI домашнего провайдера на :443; в офисе путь другой, поэтому там работает."
        )
        log(
            "  не чините sing-box — добавьте второй listen (например :8443) или "
            "полностью опустите туннель Amnezia (служба AmneziaWGTunnel), не только GUI."
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

    client.paths.var_dir.mkdir(parents=True, exist_ok=True)
    client.paths.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = client.paths.var_dir / "sandbox-sing-box.json"
    log_path = client.paths.logs_dir / "sandbox-sing-box.log"

    # 1) Direct VLESS — expected to die if Amnezia filters sing-box.
    log("— 3 прямой VLESS (sing-box → VPS), как обычный клиент")
    _write_box(
        cfg_path,
        log_path,
        _vless_cfg(transport, server=host, port=port, log_path=log_path),
    )
    proc = _start_box(exe, cfg_path)
    direct_ok = False
    try:
        if not procutil.wait_port_open("127.0.0.1", SANDBOX_SOCKS, timeout=6.0):
            note(False, "прямой SOCKS", f"не открылся :{SANDBOX_SOCKS}")
        else:
            err = socks_https_probe(SANDBOX_SOCKS, timeout=6.0 if active else 12.0)
            direct_ok = not err
            note(direct_ok, "3 VLESS sing-box", err or "sing-box сам дошёл до VPS")
    finally:
        _stop_box(proc)
        time.sleep(0.4)

    if active:
        log("— слои (чужой туннель поднят, мы его не трогали):")
        log("  1 TCP python  — путь до VPS с underlay NIC")
        log("  2 TLS → VPS   — отвечает ли Reality хоть чем-то")
        log("  3 VLESS       — тут видно, режет ли чужой VPN наш sing-box")
        log(
            "  в таблице несколько default: чужой туннель (metric 5) обходит Wi-Fi. "
            "При on клиент снимает чужие 0.0.0.0/0 и оставляет свой шлюз."
        )
        if not direct_ok:
            log(
                "итог: слой 1 живой, слой 3 мёртв — чужой поднятый туннель "
                "перехватывает выход. Опустите его туннель (или дайте клиенту "
                "снять чужой default) и повторите."
            )
            return 0
        log("итог: VLESS работает даже рядом с чужим туннелем")
        return 0

    if direct_ok:
        log("— полный набор через прямой VLESS")
        _write_box(
            cfg_path,
            log_path,
            _vless_cfg(transport, server=host, port=port, log_path=log_path),
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
        return _finish(failed, others, direct_ok, relay_ok=None, log=log)

    # 2) Relay: sing-box → 127.0.0.1 → python → VPS
    relay_ok = False
    for vision in (True, False):
        label = "vision" if vision else "без flow"
        log(f"— VLESS через реле python ({label})")
        relay = _TcpRelay(host, port, bind_ip=local_ip)
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
                    f"127.0.0.1:{relay.port} → {host}:{port} src={local_ip or 'auto'}",
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
    log("песочница остановлена")

    if failed:
        tail = _tail(log_path)
        if tail:
            log("  журнал sing-box:")
            for ln in tail:
                log(f"    {ln}")
    return _finish(failed, others, direct_ok, relay_ok, log=log)


def _write_box(cfg_path: Path, log_path: Path, box_cfg: dict) -> None:
    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass
    cfg_path.write_text(json.dumps(box_cfg, indent=2) + "\n", encoding="utf-8")


def _finish(
    failed: int,
    others: list[str],
    direct_ok: bool,
    relay_ok: bool | None,
    *,
    log: LogFn,
) -> int:
    if others and relay_ok and not direct_ok:
        log(
            "итог: протокол живой. Amnezia не выпускает sing-box/ergoms-tun "
            "на VPS; GUI с TUN рядом с ней работать не будет"
        )
        return 0
    if others and not direct_ok and not relay_ok:
        log(
            "итог: рядом с Amnezia TCP до VPS есть, а Reality не отвечает (реле ↑есть ↓0). "
            "Amnezia режет и процесс tun, и сам handshake. Это не конфликт маршрутов TUN."
        )
        return 1
    if failed:
        log(f"итог: {failed} провал(а)")
        return 1
    log("итог: VLESS с этой сети живой (TUN в песочнице не проверяется)")
    return 0


def _tail(path: Path, n: int = 12) -> list[str]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln for ln in lines[-n:] if ln.strip()]
