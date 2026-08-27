"""MODE=singbox: one sing-box process (VLESS+Reality via Squid + mixed + optional TUN)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from desktop import procutil
from desktop.tun import (
    RUSTDESK_PORTS,
    TunManager,
    _direct_python_paths,
    _resolve_host,
    detect_bind_interface,
)
from lib.http_via_socks import bypass_to_singbox

LogFn = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def parse_corporate_proxy(proxy: str) -> tuple[str, int]:
    raw = (proxy or "").replace("http://", "").replace("https://", "").strip("/")
    host, _, port_s = raw.partition(":")
    return host.strip(), int(port_s or "3128")


def require_transport(cfg: dict[str, Any]) -> dict[str, Any]:
    tr = cfg.get("transport")
    if not isinstance(tr, dict):
        raise RuntimeError(
            "MODE=singbox requires config.json transport{} "
            "(uuid, public_key, short_id, server_name) — run VPS bootstrap_singbox_443.sh"
        )
    uuid = str(tr.get("uuid") or "").strip()
    pub = str(tr.get("public_key") or "").strip()
    short_id = str(tr.get("short_id") or "").strip()
    sni = str(tr.get("server_name") or "www.cloudflare.com").strip()
    typ = str(tr.get("type") or "vless-reality").strip().lower()
    if typ not in ("vless-reality", "vless", "reality"):
        raise RuntimeError(f"Unsupported transport.type={typ} (use vless-reality)")
    if not uuid or "REPLACE" in uuid.upper() or len(uuid) < 8:
        raise RuntimeError("transport.uuid missing — paste from VPS bootstrap output")
    if not pub or "REPLACE" in pub.upper():
        raise RuntimeError("transport.public_key missing — paste from VPS bootstrap")
    port = int(tr.get("port") or 443)
    return {
        "uuid": uuid,
        "public_key": pub,
        "short_id": short_id,
        "server_name": sni,
        "port": port,
        "type": "vless-reality",
    }


class SingboxModeManager:
    """Run sing-box with VLESS via corporate HTTP CONNECT (+ local mixed/TUN)."""

    def __init__(
        self,
        var_dir: Path,
        tools_dir: Path,
        logs_dir: Path,
        log: LogFn = _noop,
    ) -> None:
        self.var_dir = var_dir
        self.tools_dir = tools_dir
        self.logs_dir = logs_dir
        self.log = log
        self.config_path = var_dir / "sing-box-mode.json"
        self.pid_path = var_dir / "sing-box-mode.pid"
        self.log_path = logs_dir / "sing-box-mode.log"
        self._tun_helper = TunManager(var_dir, tools_dir, logs_dir, log=log)
        self._pid_scan_at = 0.0
        self._pid_scan_result: int | None = None

    def find_sing_box(self, explicit: str = "") -> Path | None:
        return self._tun_helper.find_sing_box(explicit)

    def ensure_downloaded(self, proxy_url: str | None = None) -> Path:
        return self._tun_helper.ensure_downloaded(proxy_url=proxy_url)

    def build_config(
        self,
        *,
        server_host: str,
        transport: dict[str, Any],
        corporate_proxy: str,
        socks_port: int,
        http_port: int,
        enable_tun: bool,
        bypass_hosts: list[str] | None = None,
        mtu: int = 1400,
        vps_proxy_ports: list[int] | None = None,
    ) -> dict[str, Any]:
        squid_host, squid_port = parse_corporate_proxy(corporate_proxy)
        if not squid_host:
            raise RuntimeError("corporate_proxy empty")

        exclude_ips: list[str] = []
        for h in (squid_host, server_host):
            ip = _resolve_host(h)
            if ip:
                exclude_ips.append(ip)

        # Prefer underlay NIC toward Squid so TUN auto_route cannot steal dials.
        bind_iface = detect_bind_interface(exclude_ips[0] if exclude_ips else squid_host)

        route_exclude = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "224.0.0.0/4",
        ]
        proc_names = [
            "sing-box",
            "sing-box.exe",
            "OpsContent.exe",
        ]
        docker_wsl_procs = [
            "vmmem",
            "vmmemWSL",
            "wsl.exe",
            "wslhost.exe",
            "wslrelay.exe",
            "wslservice.exe",
            "WSLService.exe",
            "vmcompute.exe",
            "vmwp.exe",
            "com.docker.backend.exe",
            "com.docker.build.exe",
            "com.docker.proxy.exe",
            "com.docker.admin.exe",
            "com.docker.dev-envs.exe",
            "Docker Desktop.exe",
            "docker.exe",
            "dockerd.exe",
            "vpnkit.exe",
            "vpnkit-bridge.exe",
        ]

        rules: list[dict[str, Any]] = []
        # Dial Squid / avoid looping VPS:443 through TUN.
        # Office RST-kills :21114/:21116 on the VPS IP — send those via VLESS :443.
        vpn_port = int(transport.get("port") or 443)
        vps_ip = _resolve_host(server_host)
        if vps_ip:
            rules.append(
                {"ip_cidr": [f"{vps_ip}/32"], "port": vpn_port, "outbound": "direct"}
            )
            rules.append(
                {
                    "ip_cidr": [f"{vps_ip}/32"],
                    "port": RUSTDESK_PORTS,
                    "outbound": "proxy",
                }
            )
            # Office RST on VPS :22. Reverse SSH and ssh-to-VPS must go via VLESS.
            ssh_ports = [int(p) for p in (vps_proxy_ports or [22]) if 1 <= int(p) <= 65535]
            if ssh_ports:
                rules.append(
                    {
                        "ip_cidr": [f"{vps_ip}/32"],
                        "port": ssh_ports if len(ssh_ports) > 1 else ssh_ports[0],
                        "outbound": "proxy",
                    }
                )
        if exclude_ips:
            rules.append(
                {"ip_cidr": [f"{ip}/32" for ip in exclude_ips], "outbound": "direct"}
            )
        bypass_suffixes, bypass_domains = bypass_to_singbox(bypass_hosts or [])
        if bypass_suffixes:
            rules.append({"domain_suffix": bypass_suffixes, "outbound": "direct"})
        if bypass_domains:
            rules.append({"domain": bypass_domains, "outbound": "direct"})
        rules.append({"port": 53, "action": "hijack-dns"})
        # HTTP/SOCKS inbounds (Docker Desktop httpproxy, git, curl) must use
        # VLESS. process_name docker→direct would steal CONNECT and send it
        # out the office NIC.
        rules.append({"inbound": ["socks-in", "http-in"], "outbound": "proxy"})
        rules.append({"process_name": docker_wsl_procs, "outbound": "direct"})
        rules.append({"process_name": proc_names, "outbound": "direct"})
        py_paths = _direct_python_paths()
        if py_paths:
            rules.append({"process_path": py_paths, "outbound": "direct"})
        rules.append({"ip_is_private": True, "outbound": "direct"})

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        mtu_val = max(1280, min(1500, int(mtu)))
        sni = str(transport["server_name"])
        vless_port = int(transport["port"])

        inbounds: list[dict[str, Any]] = [
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
        ]
        if enable_tun:
            inbounds.append(
                {
                    "type": "tun",
                    "tag": "tun-in",
                    "interface_name": "ops-content-tun",
                    "address": ["172.19.0.1/30"],
                    "mtu": mtu_val,
                    "auto_route": True,
                    "strict_route": False,
                    "stack": "system",
                    "route_exclude_address": route_exclude,
                }
            )

        return {
            "log": {
                "level": "info",
                "timestamp": True,
                "output": str(self.log_path).replace("\\", "/"),
            },
            "dns": {
                "servers": [
                    {
                        "tag": "dns-proxy",
                        "address": "https://1.1.1.1/dns-query",
                        "detour": "proxy",
                    },
                    {"tag": "dns-local", "address": "local", "detour": "direct"},
                ],
                "rules": [
                    {
                        "process_name": docker_wsl_procs,
                        "server": "dns-local",
                    },
                    *(
                        [{"domain_suffix": bypass_suffixes, "server": "dns-local"}]
                        if bypass_suffixes
                        else []
                    ),
                    *(
                        [{"domain": bypass_domains, "server": "dns-local"}]
                        if bypass_domains
                        else []
                    ),
                    {
                        "domain_suffix": [".local", ".lan", ".internal", ".localhost"],
                        "server": "dns-local",
                    },
                ],
                "final": "dns-proxy",
                "strategy": "prefer_ipv4",
            },
            "inbounds": inbounds,
            "outbounds": [
                {
                    "type": "http",
                    "tag": "squid",
                    "server": squid_host,
                    "server_port": int(squid_port),
                    **({"bind_interface": bind_iface} if bind_iface else {}),
                },
                {
                    "type": "vless",
                    "tag": "proxy",
                    "server": server_host,
                    "server_port": vless_port,
                    "uuid": transport["uuid"],
                    "flow": "xtls-rprx-vision",
                    "packet_encoding": "xudp",
                    "tls": {
                        "enabled": True,
                        "server_name": sni,
                        "utls": {"enabled": True, "fingerprint": "chrome"},
                        "reality": {
                            "enabled": True,
                            "public_key": transport["public_key"],
                            "short_id": transport["short_id"],
                        },
                    },
                    "detour": "squid",
                },
                {
                    "type": "direct",
                    "tag": "direct",
                    **({"bind_interface": bind_iface} if bind_iface else {}),
                },
                {"type": "block", "tag": "block"},
            ],
            "route": {
                "auto_detect_interface": True,
                **({"default_interface": bind_iface} if bind_iface else {}),
                "final": "proxy",
                "rules": [
                    {"inbound": ["socks-in", "http-in"], "action": "sniff", "timeout": "1s"},
                    *(
                        [{"inbound": ["tun-in"], "action": "sniff", "timeout": "1s"}]
                        if enable_tun
                        else []
                    ),
                    *rules,
                ],
            },
        }

    def running(self) -> bool:
        return self.pid() is not None

    def pid(self) -> int | None:
        if self.pid_path.is_file():
            try:
                pid = int(self.pid_path.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = None
            if pid and procutil.pid_alive(pid):
                return pid
        now = time.monotonic()
        if now - self._pid_scan_at < 2.0 and self._pid_scan_result:
            if procutil.pid_alive(self._pid_scan_result):
                return self._pid_scan_result
        found = self._find_pid()
        self._pid_scan_at = now
        self._pid_scan_result = found
        if found:
            try:
                self.pid_path.write_text(str(found), encoding="utf-8")
            except OSError:
                # Root-owned pid after `sudo on` — status as user must still work.
                pass
            return found
        return None

    def tun_active(self) -> bool:
        """True if our mode process is up and TUN iface exists."""
        if not self.running():
            return False
        return self._tun_helper._tun_iface_present()  # noqa: SLF001

    def start(
        self,
        *,
        server_host: str,
        transport: dict[str, Any],
        corporate_proxy: str,
        socks_port: int,
        http_port: int,
        enable_tun: bool,
        sing_box_path: str = "",
        elevate: bool = True,
        bypass_hosts: list[str] | None = None,
        mtu: int = 1400,
        vps_proxy_ports: list[int] | None = None,
        force_restart: bool = False,
    ) -> None:
        exe = self.find_sing_box(sing_box_path)
        if not exe:
            raise RuntimeError(
                "sing-box не найден. Выполните: ops-content download-sing-box"
            )

        cfg = self.build_config(
            server_host=server_host,
            transport=transport,
            corporate_proxy=corporate_proxy,
            socks_port=socks_port,
            http_port=http_port,
            enable_tun=enable_tun,
            bypass_hosts=bypass_hosts,
            mtu=mtu,
            vps_proxy_ports=vps_proxy_ports,
        )
        config_text = json.dumps(cfg, indent=2)
        if self.running():
            if not force_restart:
                old = (
                    self.config_path.read_text(encoding="utf-8")
                    if self.config_path.is_file()
                    else ""
                )
                if old == config_text:
                    self.log(f"singbox mode already running (pid={self.pid()})")
                    return
            self.log("singbox mode config changed — restart")
            self.stop()

        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(config_text, encoding="utf-8")

        need_admin = bool(enable_tun and elevate)
        self.log(
            f"Starting MODE=singbox → VLESS {server_host}:{transport['port']} "
            f"via Squid; socks=:{socks_port} http=:{http_port} tun={int(enable_tun)}"
        )
        if need_admin:
            self.log("Нужны права администратора для TUN")

        pid = self._launch(exe, elevate=need_admin)
        if pid:
            self.pid_path.write_text(str(pid), encoding="utf-8")

        deadline = time.monotonic() + (12.0 if enable_tun else 8.0)
        interval = 0.05
        while time.monotonic() < deadline:
            if _port_open("127.0.0.1", socks_port) and _port_open(
                "127.0.0.1", http_port
            ):
                if enable_tun:
                    if self.tun_active() or self.running():
                        # iface may lag a bit after ports open
                        if self.tun_active() or time.monotonic() + 0.5 > deadline:
                            self.log("MODE=singbox активен (mixed + TUN → VLESS)")
                            return
                else:
                    self.log("MODE=singbox активен (mixed → VLESS)")
                    return
            time.sleep(interval)
            interval = min(interval * 1.3, 0.25)

        if _port_open("127.0.0.1", socks_port):
            self.log("MODE=singbox: SOCKS up (TUN may still be starting)")
            return

        raise RuntimeError(
            "sing-box mode не поднял SOCKS/HTTP. "
            f"См. {self.log_path}. Нужен download-sing-box и верные transport.*"
        )

    def stop(self) -> None:
        pid = self.pid()
        if pid:
            procutil.kill_pid(pid)
            self.log(f"sing-box mode pid={pid} stopped")
        for orphan in procutil.pids_cmdline_match("sing-box-mode.json", cache=False):
            if orphan != pid:
                procutil.kill_pid(orphan)
        self._pid_scan_at = 0.0
        self._pid_scan_result = None
        self.pid_path.unlink(missing_ok=True)

    def _launch(self, exe: Path, *, elevate: bool) -> int | None:
        args = [str(exe), "run", "-c", str(self.config_path)]
        if sys.platform == "win32" and elevate and not procutil.is_admin():
            return self._start_elevated_win(exe, self.config_path)

        if (
            sys.platform != "win32"
            and elevate
            and hasattr(os, "geteuid")
            and os.geteuid() != 0
        ):
            return self._start_elevated_linux(args)

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=procutil.creationflags(),
        )
        return proc.pid

    def _start_elevated_win(self, exe: Path, config: Path) -> int | None:
        import ctypes

        params = f'run -c "{config}"'
        rc = int(
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", str(exe), params, str(exe.parent), 0
            )
        )
        if rc <= 32:
            raise RuntimeError(
                f"UAC для sing-box mode не удался (код {rc}). "
                "Запустите от администратора или TUN=0."
            )
        time.sleep(1.0)
        return self._find_pid()

    def _start_elevated_linux(self, args: list[str]) -> int | None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        for wrapper in (
            ["pkexec", *args],
            ["sudo", "-n", *args],
            ["sudo", *args],
        ):
            try:
                proc = subprocess.Popen(
                    wrapper,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(0.5)
                if proc.poll() is None or self._find_pid():
                    self.log(f"singbox mode via {wrapper[0]}")
                    return proc.pid if proc.poll() is None else self._find_pid()
            except FileNotFoundError:
                continue
        raise RuntimeError(
            "Не удалось запустить sing-box mode с root (pkexec/sudo). "
            "Или TUN=0, или: sudo python -m desktop on"
        )

    def _find_pid(self) -> int | None:
        for pid in procutil.pids_cmdline_match("sing-box-mode.json"):
            return pid
        return None


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
