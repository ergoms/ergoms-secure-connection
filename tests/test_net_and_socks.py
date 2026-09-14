"""Characterization tests for shared primitives (no live network)."""

from __future__ import annotations

import json
import socket
import struct
from pathlib import Path

import pytest

from desktop.config_io import (
    default_config_template,
    merge_imported_config,
    normalize_dial,
    save_config,
)
from desktop.net_host import resolve_host
from desktop.singbox_mode import choose_dial
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
    assert normalize_dial("auto") == "amneziawg"
    assert normalize_dial("") == "amneziawg"


def test_tun_split_cmds_cover_both_halves() -> None:
    from desktop.tun import install_tun_split_default

    cmds = install_tun_split_default(53)
    joined = "\n".join(cmds)
    assert "route add 0.0.0.0 mask 128.0.0.0 172.19.0.1" in joined
    assert "route add 128.0.0.0 mask 128.0.0.0 172.19.0.1" in joined
    assert "if 53" in joined
    onlink = "\n".join(install_tun_split_default(53, hop="0.0.0.0"))
    assert "route add 0.0.0.0 mask 128.0.0.0 0.0.0.0" in onlink


def test_win_kill_switch_blackhole_is_onlink_loopback() -> None:
    from desktop.kill_switch import _cmds_win_install

    cmds = _cmds_win_install(["193.23.202.147"], "10.193.0.1", blackhole=True)
    adds = [c for c in cmds if c.startswith("route add 0.0.0.0") or c.startswith("route add 128.")]
    assert adds
    assert all(" 0.0.0.0 metric 512 if " in c for c in adds)
    assert not any("127.0.0.1 metric" in c for c in adds)
    live = _cmds_win_install(["193.23.202.147"], "10.193.0.1", blackhole=False)
    assert any("route delete 0.0.0.0 mask 128.0.0.0" in c for c in live)
    assert not any(c.startswith("route add 0.0.0.0 mask 128") for c in live)


def test_choose_dial_defaults_and_office_choice() -> None:
    assert choose_dial({}, office=False) == "amneziawg"
    assert choose_dial({}, office=True) == "vless-reality"
    assert choose_dial({"dial": "amneziawg"}, office=True) == "amneziawg"
    assert choose_dial({"dial": "vless-reality"}, office=False) == "vless-reality"
    assert choose_dial({"dial": "hysteria2"}, office=True) == "hysteria2"


def test_underlay_ifaces_skips_loopback() -> None:
    from desktop.tun import underlay_ifaces

    rows = underlay_ifaces()
    assert isinstance(rows, list)
    for idx, metric, name in rows:
        assert idx > 0
        assert metric > 0
        assert "loopback" not in name.lower()


def test_exit_probe_target_by_mode() -> None:
    from desktop.watchdog import exit_probe_target

    assert exit_probe_target(office=True) == ("1.1.1.1", "/cdn-cgi/trace")
    assert exit_probe_target(office=False) == ("1.1.1.1", "/cdn-cgi/trace")


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
    assert default_config_template()["transport"]["dial"] == "amneziawg"
    assert settings_defaults()["trDial"] == "amneziawg"

    settings["trDial"] = "amneziawg"
    office = apply_settings_to_cfg(default_config_template(), get, corporate=True)
    assert office["corporate"] is True
    assert office["transport"]["dial"] == "amneziawg"
    assert office["socks_scope"] == "full"


def test_merge_empty_does_not_wipe_secrets() -> None:
    base = default_config_template()
    base["server"]["host"] = "vps.example"
    base["blocked_hosts"] = ["keep.example"]
    base["proxy_bypass"] = ["*.tu-bryansk.ru", "*.local"]
    base["transport"]["uuid"] = "keep-uuid"
    base["transport"]["hysteria2"]["password"] = "hy2-secret"
    base["transport"]["amneziawg"]["private_key"] = "awg-priv"
    base["transport"]["amneziawg"]["jc"] = 10
    incoming = {
        "server": {"host": ""},
        "blocked_hosts": [],
        "proxy_bypass": [],
        "transport": {
            "uuid": "",
            "hysteria2": {"password": ""},
            "amneziawg": {"private_key": "", "jc": 10},
        },
    }
    out = merge_imported_config(base, incoming)
    assert out["server"]["host"] == "vps.example"
    assert out["blocked_hosts"] == ["keep.example"]
    assert out["proxy_bypass"] == ["*.tu-bryansk.ru", "*.local"]
    assert out["transport"]["uuid"] == "keep-uuid"
    assert out["transport"]["hysteria2"]["password"] == "hy2-secret"
    assert out["transport"]["amneziawg"]["private_key"] == "awg-priv"
    assert out["transport"]["amneziawg"]["jc"] == 10


def test_apply_settings_empty_form_keeps_disk() -> None:
    cfg = default_config_template()
    cfg["server"]["host"] = "vps.example"
    cfg["corporate_proxy"] = "10.16.0.8:3128"
    cfg["proxy_bypass"] = ["*.tu-bryansk.ru", "*.local"]
    cfg["tun"]["sing_box_path"] = "C:/tools/sing-box.exe"
    cfg["transport"]["uuid"] = "keep-uuid"
    cfg["transport"]["public_key"] = "keep-pk"
    cfg["transport"]["hysteria2"]["password"] = "hy2-secret"
    cfg["transport"]["amneziawg"]["private_key"] = "awg-priv"
    cfg["transport"]["amneziawg"]["peer_public_key"] = "awg-pub"
    cfg["transport"]["amneziawg"]["jc"] = 10
    cfg["transport"]["amneziawg"]["h1"] = "111"

    def get(key: str) -> object:
        return settings_defaults()[key]

    out = apply_settings_to_cfg(cfg, get, corporate=False)
    assert out["server"]["host"] == "vps.example"
    assert out["corporate_proxy"] == "10.16.0.8:3128"
    assert out["proxy_bypass"] == ["*.tu-bryansk.ru", "*.local"]
    assert out["tun"]["sing_box_path"] == "C:/tools/sing-box.exe"
    assert out["transport"]["uuid"] == "keep-uuid"
    assert out["transport"]["public_key"] == "keep-pk"
    assert out["transport"]["hysteria2"]["password"] == "hy2-secret"
    assert out["transport"]["amneziawg"]["private_key"] == "awg-priv"
    assert out["transport"]["amneziawg"]["peer_public_key"] == "awg-pub"
    assert out["transport"]["amneziawg"]["jc"] == 10
    assert out["transport"]["amneziawg"]["h1"] == "111"


def test_save_config_empty_payload_keeps_disk(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    live = default_config_template()
    live["server"]["host"] = "vps.example"
    live["transport"]["uuid"] = "keep-uuid"
    live["transport"]["amneziawg"]["private_key"] = "awg-priv"
    live["blocked_hosts"] = ["keep.example"]
    path.write_text(json.dumps(live), encoding="utf-8")
    save_config(path, {"transport": {"uuid": "", "amneziawg": {"private_key": ""}}})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["server"]["host"] == "vps.example"
    assert saved["transport"]["uuid"] == "keep-uuid"
    assert saved["transport"]["amneziawg"]["private_key"] == "awg-priv"
    assert saved["blocked_hosts"] == ["keep.example"]
