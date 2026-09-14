"""Characterization tests for shared primitives (no live network)."""

from __future__ import annotations

import socket
import struct

import pytest

from desktop.config_io import default_config_template, normalize_dial
from desktop.net_host import resolve_host
from desktop.ui.settings_map import apply_settings_to_cfg, cfg_to_settings, settings_defaults
from lib.http_connect import parse_proxy
from lib.netutil import port_open
from lib.socks5 import consume_bind_addr, handshake


class _FakeSock:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent = b""
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        data = self._chunks.pop(0)
        return data[:n]

    def close(self) -> None:
        self.closed = True

    def settimeout(self, _t: float) -> None:
        pass


def test_normalize_dial_aliases() -> None:
    assert normalize_dial("vless") == "vless-reality"
    assert normalize_dial("reality") == "vless-reality"
    assert normalize_dial("hy2") == "hysteria2"
    assert normalize_dial("awg") == "amneziawg"
    assert normalize_dial("auto") == "hysteria2"
    assert normalize_dial("") == "hysteria2"


def test_port_open_closed_port() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert port_open("127.0.0.1", port, timeout=0.15) is False


def test_port_open_listening() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert port_open("127.0.0.1", port, timeout=0.5) is True
    finally:
        srv.close()


def test_resolve_host_ipv4() -> None:
    assert resolve_host("1.2.3.4") == "1.2.3.4"
    assert resolve_host("") is None
    assert resolve_host("   ") is None


def test_socks5_handshake_ipv4_bind() -> None:
    bind = b"\x05\x00\x00\x01" + socket.inet_aton("10.0.0.1") + struct.pack("!H", 1080)
    sock = _FakeSock([b"\x05\x00", bind[:4], bind[4:]])
    handshake(sock, "1.1.1.1", 443, prefer_ipv4_atyp=True)
    assert sock.sent.startswith(b"\x05\x01\x00")
    assert b"\x05\x01\x00\x01" in sock.sent


def test_socks5_handshake_greeting_fail() -> None:
    sock = _FakeSock([b"\x05\x01"])
    with pytest.raises(OSError, match="greeting"):
        handshake(sock, "example.com", 443)


def test_socks5_consume_bad_atyp() -> None:
    sock = _FakeSock([])
    with pytest.raises(OSError, match="bad atyp"):
        consume_bind_addr(sock, 9)


def test_parse_proxy() -> None:
    assert parse_proxy("10.16.0.8:3128") == ("10.16.0.8", 3128)
    assert parse_proxy("http://proxy.local") == ("proxy.local", 3128)
    assert parse_proxy("https://proxy.local:8080/") == ("proxy.local", 8080)


def test_default_config_has_blocked_hosts() -> None:
    cfg = default_config_template()
    assert "github.com" in cfg["blocked_hosts"]
    assert cfg["proxy_bypass"] == ["*.local", "*.lan"]


def test_config_skeleton_has_local_inbounds() -> None:
    from desktop.singbox_mode import config_skeleton, local_inbounds

    box = config_skeleton(log_path="x.log", socks_port=1080, http_port=1088, simple_dns=True)
    assert box["inbounds"] == local_inbounds(1080, 1088)
    assert box["dns"]["final"] == "dns-proxy"
    assert "log" in box


def test_watchdog_does_not_import_client() -> None:
    import desktop.watchdog as wd

    assert "desktop.client" not in getattr(wd, "__dict__", {})
    assert not hasattr(wd, "OpsClient")


def test_settings_map_roundtrip() -> None:
    cfg = default_config_template()
    cfg["server"]["host"] = "vps.example"
    cfg["transport"]["uuid"] = "u-1"
    cfg["transport"]["dial"] = "hysteria2"
    settings = cfg_to_settings(cfg)
    assert settings["serverHost"] == "vps.example"
    assert settings["trUuid"] == "u-1"
    assert settings["trDial"] == "hysteria2"
    defaults = settings_defaults()
    assert set(defaults) <= set(settings)

    def get(key: str) -> object:
        return settings[key]

    out = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert out["server"]["host"] == "vps.example"
    assert out["transport"]["uuid"] == "u-1"
    assert out["transport"]["dial"] == "hysteria2"
