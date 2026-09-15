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
from lib.connect import allowed_target
from lib.http_connect import parse_proxy
from lib.netutil import parse_endpoint, port_open
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
    assert normalize_dial("hy2") == "amneziawg"
    assert normalize_dial("hysteria2") == "amneziawg"
    assert normalize_dial("awg") == "amneziawg"
    assert normalize_dial("auto") == "amneziawg"
    assert normalize_dial("") == "amneziawg"


def test_tun_split_cmds_cover_both_halves() -> None:
    from desktop.tun import install_tun_split_default, remove_tun_split_default

    cmds = install_tun_split_default(53)
    joined = "\n".join(cmds)
    assert "route add 0.0.0.0 mask 128.0.0.0 172.19.0.1" in joined
    assert "route add 128.0.0.0 mask 128.0.0.0 172.19.0.1" in joined
    assert "if 53" in joined
    onlink = "\n".join(install_tun_split_default(53, hop="0.0.0.0"))
    assert "route add 0.0.0.0 mask 128.0.0.0 0.0.0.0" in onlink
    removed = "\n".join(remove_tun_split_default(windows=True))
    assert "route delete 0.0.0.0 mask 128.0.0.0 172.19.0.1" in removed
    assert "route delete 128.0.0.0 mask 128.0.0.0 172.19.0.1" in removed
    assert "route delete 0.0.0.0 mask 128.0.0.0 172.19.0.2" in removed
    linux = "\n".join(remove_tun_split_default(windows=False))
    assert "ip route del 0.0.0.0/1 via 172.19.0.1" in linux
    assert "ip route del 128.0.0.0/1 via 172.19.0.2" in linux


def test_win_kill_switch_blackhole_is_onlink_loopback() -> None:
    from desktop.kill_switch import _cmds_win_install

    cmds = _cmds_win_install(["203.0.113.10"], "10.193.0.1", blackhole=True)
    adds = [c for c in cmds if c.startswith("route add 0.0.0.0") or c.startswith("route add 128.")]
    assert adds
    assert all(" 0.0.0.0 metric 512 if " in c for c in adds)
    assert not any("127.0.0.1 metric" in c for c in adds)
    live = _cmds_win_install(["203.0.113.10"], "10.193.0.1", blackhole=False)
    assert any("route delete 0.0.0.0 mask 128.0.0.0" in c for c in live)
    assert not any(c.startswith("route add 0.0.0.0 mask 128") for c in live)


def test_win_kill_switch_remove_drops_tun_split() -> None:
    from desktop.kill_switch import _cmds_linux_remove, _cmds_win_remove

    win = _cmds_win_remove(["203.0.113.10"], "10.193.0.1")
    joined = "\n".join(win)
    assert "route delete 0.0.0.0 mask 128.0.0.0 172.19.0.1" in joined
    assert "route delete 128.0.0.0 mask 128.0.0.0 172.19.0.2" in joined
    linux = "\n".join(_cmds_linux_remove(["203.0.113.10"], "10.193.0.1"))
    assert "ip route del 0.0.0.0/1 via 172.19.0.1" in linux
    assert "ip route del 0.0.0.0/1 dev lo" in linux


def test_choose_dial_defaults_and_office_choice() -> None:
    assert choose_dial({}, office=False) == "amneziawg"
    assert choose_dial({}, office=True) == "vless-reality"
    assert choose_dial({"dial": "amneziawg"}, office=True) == "amneziawg"
    assert choose_dial({"dial": "vless-reality"}, office=False) == "vless-reality"
    assert choose_dial({"dial": "hysteria2"}, office=True) == "amneziawg"


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
    assert parse_proxy("192.0.2.10:3128") == ("192.0.2.10", 3128)
    assert parse_proxy("http://proxy.local") == ("proxy.local", 3128)
    assert parse_proxy("https://proxy.local:8080/") == ("proxy.local", 8080)


def test_parse_endpoint_and_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_endpoint("socks5://127.0.0.1:1080", 1080, default_host="127.0.0.1") == (
        "127.0.0.1",
        1080,
    )
    monkeypatch.delenv("ERGOMS_SC_CONNECT_ALLOW", raising=False)
    monkeypatch.delenv("OPS_CONTENT_CONNECT_ALLOW", raising=False)
    assert allowed_target("vps.example", 22)
    monkeypatch.setenv("ERGOMS_SC_CONNECT_ALLOW", "vps.example:22")
    assert allowed_target("vps.example", 22)
    assert not allowed_target("other.example", 22)


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


def test_teardown_if_dirty_skips_live_vpn() -> None:
    from desktop.integrations import IntegrationOps

    n = {"teardown": 0}

    class Host(IntegrationOps):
        def _override_markers(self) -> bool:
            return True

        def _vpn_process_up(self) -> bool:
            return True

        def teardown_overrides(self) -> None:
            n["teardown"] += 1

    Host().teardown_overrides_if_dirty()
    assert n["teardown"] == 0

    class Dead(Host):
        def _vpn_process_up(self) -> bool:
            return False

    Dead().teardown_overrides_if_dirty()
    assert n["teardown"] == 1


def test_watchdog_reconnect_keeps_pac(monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop.watchdog import TunnelWatchdog

    stops: list[bool] = []

    class Host:
        log = staticmethod(lambda _m: None)
        paths = type("P", (), {"var_dir": None})()
        singbox = type("S", (), {"running": staticmethod(lambda: False)})()
        tun = type("T", (), {"running": staticmethod(lambda: False)})()
        reverse_ssh = type("R", (), {"running": staticmethod(lambda: False)})()

        def reload_env(self) -> None:
            return None

        def enable(self, *, spawn_watchdog: bool = True) -> None:
            del spawn_watchdog

        def enable_tun(self, *, persist: bool = True) -> None:
            del persist

        def stop_singbox_mode(self, *, teardown: bool = True) -> None:
            stops.append(teardown)

        def config(self) -> dict:
            return {}

        def _ensure_kill_switch(self, cfg: dict) -> list[str]:
            del cfg
            return []

        def _maybe_start_reverse_ssh(self, cfg: dict | None = None) -> None:
            del cfg

    monkeypatch.setattr(
        "desktop.watchdog.health_problem", lambda *_a, **_k: "SOCKS :1080 down"
    )
    monkeypatch.setattr("desktop.watchdog.get_watchdog_interval", lambda: 5)
    wd = TunnelWatchdog(Host())  # type: ignore[arg-type]
    wd._reconnect(tun_only=False)  # noqa: SLF001
    assert stops == [False]


def test_pac_server_replace_keeps_listener() -> None:
    import socket

    from desktop.pac_serve import PacServer

    srv = PacServer()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    srv.start(b"v1", listen_host="127.0.0.1", listen_port=port)
    try:
        assert srv.running and srv.port == port
        srv.replace_pac(b"v2")
        assert srv.running and srv.port == port
    finally:
        srv.stop()


def test_enable_browser_pac_skips_notify_when_set(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from desktop import win_proxy

    monkeypatch.setattr(win_proxy, "_is_windows", lambda: True)
    monkeypatch.setattr(win_proxy, "pac_url_active", lambda _url: True)
    monkeypatch.setattr(win_proxy, "backup_win_proxy", lambda *_a, **_k: None)
    notified = {"n": 0}
    monkeypatch.setattr(
        win_proxy, "notify_proxy_change", lambda: notified.__setitem__("n", notified["n"] + 1)
    )
    win_proxy.enable_browser_pac(
        1088,
        "full",
        3,
        tmp_path / "bak.json",
        pac_url="http://127.0.0.1:1089/proxy.pac",
    )
    assert notified["n"] == 0


def test_watchdog_reconnects_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop.watchdog import TunnelWatchdog

    class Host:
        log = staticmethod(lambda _m: None)

        def reload_env(self) -> None:
            return None

        def enable(self, *, spawn_watchdog: bool = True) -> None:
            del spawn_watchdog

        def enable_tun(self, *, persist: bool = True) -> None:
            del persist

        def stop_singbox_mode(self) -> None:
            return None

    reconnects = {"n": 0}

    def fake_health(_client, *, probe: bool = True) -> str:
        del probe
        return "sing-box down"

    monkeypatch.setattr("desktop.watchdog.health_problem", fake_health)
    monkeypatch.setattr("desktop.watchdog.get_watchdog_enabled", lambda: True)
    monkeypatch.setattr("desktop.watchdog.get_watchdog_interval", lambda: 5)
    monkeypatch.setattr("desktop.watchdog.get_kill_switch", lambda: False)
    wd = TunnelWatchdog(Host())  # type: ignore[arg-type]
    wd.set_desired(True)
    wd._reconnect = lambda **_k: reconnects.__setitem__("n", reconnects["n"] + 1)  # noqa: SLF001
    for _ in range(12):
        wd._next_ok_at = 0.0  # noqa: SLF001
        wd.tick()
    assert reconnects["n"] == 12
    assert wd._fail_streak == 12  # noqa: SLF001


def test_settings_map_roundtrip() -> None:
    cfg = default_config_template()
    cfg["server"]["host"] = "vps.example"
    cfg["transport"]["uuid"] = "u-1"
    cfg["transport"]["dial"] = "vless-reality"
    settings = cfg_to_settings(cfg)
    assert settings["serverHost"] == "vps.example"
    assert settings["trUuid"] == "u-1"
    assert settings["trDial"] == "vless-reality"
    defaults = settings_defaults()
    assert set(defaults) <= set(settings)

    def get(key: str) -> object:
        return settings[key]

    out = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert out["server"]["host"] == "vps.example"
    assert out["transport"]["uuid"] == "u-1"
    assert out["transport"]["dial"] == "vless-reality"
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
    base["proxy_bypass"] = ["*.intranet.example", "*.local"]
    base["transport"]["uuid"] = "keep-uuid"
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
    assert out["proxy_bypass"] == ["*.intranet.example", "*.local"]
    assert out["transport"]["uuid"] == "keep-uuid"
    assert "hysteria2" not in out["transport"]
    assert out["transport"]["amneziawg"]["private_key"] == "awg-priv"
    assert out["transport"]["amneziawg"]["jc"] == 10
    incoming_awg = {
        "transport": {
            "amneziawg": {"private_key": "from-json", "peer_public_key": "from-json-pub"}
        }
    }
    skipped = merge_imported_config(base, incoming_awg)
    assert skipped["transport"]["amneziawg"]["private_key"] == "awg-priv"


def test_mode_toggle_keeps_user_proxy_and_bypass() -> None:
    from desktop.ui.settings_map import apply_mode_to_settings

    data = {
        "corporateProxy": "10.16.0.8:3128",
        "proxyBypass": "*.local, *.lan, *.tu-bryansk.ru",
        "trDial": "vless-reality",
        "gitProxy": True,
        "dockerProxy": True,
        "tunAuto": True,
        "killSwitch": True,
    }

    def get(key: str) -> object:
        return data.get(key)

    def put(key: str, value: object) -> None:
        data[key] = value

    apply_mode_to_settings(get, put, corporate=False)
    assert data["corporate"] is False
    assert data["useProxy"] is False
    assert data["corporateProxy"] == "10.16.0.8:3128"
    assert data["proxyBypass"] == "*.local, *.lan, *.tu-bryansk.ru"
    assert data["trDial"] == "vless-reality"
    assert data["gitProxy"] is True
    assert data["dockerProxy"] is True

    apply_mode_to_settings(get, put, corporate=True)
    assert data["corporate"] is True
    assert data["useProxy"] is True
    assert data["corporateProxy"] == "10.16.0.8:3128"
    assert data["proxyBypass"] == "*.local, *.lan, *.tu-bryansk.ru"
    assert data["trDial"] == "vless-reality"


def test_mode_switch_save_keeps_disk_exceptions() -> None:
    cfg = default_config_template()
    cfg["corporate"] = True
    cfg["corporate_proxy"] = "10.16.0.8:3128"
    cfg["proxy_bypass"] = ["*.local", "*.lan", "*.tu-bryansk.ru"]
    cfg["transport"]["dial"] = "vless-reality"
    cfg["git_proxy"] = True
    cfg["docker_proxy"] = True
    settings = cfg_to_settings(cfg)

    def get(key: str) -> object:
        return settings[key]

    from desktop.ui.settings_map import apply_mode_to_settings

    apply_mode_to_settings(get, settings.__setitem__, corporate=False)
    out = apply_settings_to_cfg(cfg, get, corporate=False)
    assert out["corporate"] is False
    assert out["corporate_proxy"] == "10.16.0.8:3128"
    assert "*.tu-bryansk.ru" in out["proxy_bypass"]
    assert out["transport"]["dial"] == "vless-reality"
    assert out["git_proxy"] is True
    assert out["docker_proxy"] is True


def test_apply_settings_empty_form_keeps_disk() -> None:
    cfg = default_config_template()
    cfg["server"]["host"] = "vps.example"
    cfg["corporate_proxy"] = "192.0.2.10:3128"
    cfg["proxy_bypass"] = ["*.intranet.example", "*.local"]
    cfg["tun"]["sing_box_path"] = "C:/tools/sing-box.exe"
    cfg["transport"]["uuid"] = "keep-uuid"
    cfg["transport"]["public_key"] = "keep-pk"
    cfg["transport"]["amneziawg"]["private_key"] = "awg-priv"
    cfg["transport"]["amneziawg"]["peer_public_key"] = "awg-pub"
    cfg["transport"]["amneziawg"]["jc"] = 10
    cfg["transport"]["amneziawg"]["h1"] = "111"

    def get(key: str) -> object:
        return settings_defaults()[key]

    out = apply_settings_to_cfg(cfg, get, corporate=False)
    assert out["server"]["host"] == "vps.example"
    assert out["corporate_proxy"] == "192.0.2.10:3128"
    assert out["proxy_bypass"] == ["*.intranet.example", "*.local"]
    assert out["tun"]["sing_box_path"] == "C:/tools/sing-box.exe"
    assert out["transport"]["uuid"] == "keep-uuid"
    assert out["transport"]["public_key"] == "keep-pk"
    assert "hysteria2" not in out["transport"]
    assert out["transport"]["amneziawg"]["private_key"] == "awg-priv"
    assert out["transport"]["amneziawg"]["peer_public_key"] == "awg-pub"
    assert out["transport"]["amneziawg"]["jc"] == 10
    assert out["transport"]["amneziawg"]["h1"] == "111"


def test_awg_conf_is_outside_json(tmp_path: Path) -> None:
    from desktop.config_io import (
        install_amnezia_conf,
        load_config,
        parse_amnezia_conf,
        read_awg_source_name,
    )

    text = (
        "[Interface]\n"
        "PrivateKey = client-priv\n"
        "Address = 10.66.66.2/32\n"
        "MTU = 1280\n"
        "Jc = 10\n"
        "H1 = 111\n"
        "\n"
        "[Peer]\n"
        "PublicKey = server-pub\n"
        "PresharedKey = psk\n"
        "Endpoint = 203.0.113.10:51821\n"
        "AllowedIPs = 0.0.0.0/0\n"
        "PersistentKeepalive = 25\n"
    )
    parsed = parse_amnezia_conf(text)
    assert parsed["private_key"] == "client-priv"
    assert parsed["peer_public_key"] == "server-pub"
    assert parsed["host"] == "203.0.113.10"
    assert parsed["port"] == 51821
    path = tmp_path / "config.json"
    save_config(path, default_config_template())
    cfg = install_amnezia_conf(path, text, source_name="pc.conf")
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert "amneziawg" not in disk["transport"]
    assert disk["transport"]["dial"] == "amneziawg"
    assert cfg["transport"]["amneziawg"]["private_key"] == "client-priv"
    loaded = load_config(path)
    assert loaded["transport"]["amneziawg"]["private_key"] == "client-priv"
    assert loaded["transport"]["amneziawg"]["jc"] == 10
    assert loaded["server"]["host"] == "203.0.113.10"
    assert read_awg_source_name(path) == "pc.conf"


def test_save_config_empty_payload_keeps_disk(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    live = default_config_template()
    live["server"]["host"] = "vps.example"
    live["transport"]["uuid"] = "keep-uuid"
    live["transport"]["amneziawg"]["private_key"] = "awg-priv"
    live["transport"]["amneziawg"]["peer_public_key"] = "awg-pub"
    live["blocked_hosts"] = ["keep.example"]
    path.write_text(json.dumps(live), encoding="utf-8")
    save_config(path, {"transport": {"uuid": "", "amneziawg": {"private_key": ""}}})
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["server"]["host"] == "vps.example"
    assert saved["transport"]["uuid"] == "keep-uuid"
    assert "amneziawg" not in saved["transport"]
    assert saved["blocked_hosts"] == ["keep.example"]
    conf = tmp_path / "amneziawg.conf"
    assert conf.is_file()
    text = conf.read_text(encoding="utf-8")
    assert "awg-priv" in text
    assert "awg-pub" in text


def test_leak_shield_apply_restore_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop.leak_shield import (
        HIVE_HKCU,
        TYPE_SZ,
        MemoryRegistry,
        apply,
        consume_browser_toast,
        restore,
        set_registry_backend,
    )

    chrome = r"Software\Policies\Google\Chrome"
    reg = MemoryRegistry()
    reg.set(HIVE_HKCU, chrome, "DnsOverHttpsMode", "automatic", TYPE_SZ)
    set_registry_backend(reg)
    monkeypatch.setattr("desktop.leak_shield._is_windows", lambda: True)
    try:
        assert apply(var_dir=tmp_path) is True
        existed, val, _typ = reg.get(HIVE_HKCU, chrome, "DnsOverHttpsMode")
        assert existed and val == "off"
        existed, val, _typ = reg.get(HIVE_HKCU, chrome, "WebRtcIPHandling")
        assert existed and val == "default_public_interface_only"
        assert consume_browser_toast(tmp_path) is True
        assert consume_browser_toast(tmp_path) is False
        assert apply(var_dir=tmp_path) is False
        restore(var_dir=tmp_path)
        existed, val, _typ = reg.get(HIVE_HKCU, chrome, "DnsOverHttpsMode")
        assert existed and val == "automatic"
        existed, _val, _typ = reg.get(HIVE_HKCU, chrome, "WebRtcIPHandling")
        assert not existed
    finally:
        set_registry_backend(None)


def test_win_tun_routes_can_call_leftover_runner() -> None:
    import desktop.connection as conn

    assert callable(conn.run_leftover_vpn_default_cmds)


def test_log_foreign_vpn_home_returns_leftover_when_not_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop.connection import ConnectionOps

    class Host(ConnectionOps):
        def log(self, msg: str) -> None:
            del msg

    cmds = ["route delete 0.0.0.0 mask 0.0.0.0 if 12"]
    monkeypatch.setattr("desktop.connection.leftover_vpn_ifaces", lambda: [(12, "Amnezia")])
    monkeypatch.setattr("desktop.connection.leftover_vpn_default_cmds", lambda: list(cmds))
    monkeypatch.setattr("desktop.connection.stale_default_cmds", lambda _gw: [])
    monkeypatch.setattr("desktop.connection.gateway_via_dest", lambda _h: "10.0.0.1")
    monkeypatch.setattr("desktop.connection.foreign_vpn_processes", lambda: [])
    monkeypatch.setattr("desktop.connection.foreign_vpn_live", lambda: [])
    monkeypatch.setattr("desktop.connection.default_route_lines", lambda: [])
    monkeypatch.setattr("desktop.procutil.is_admin", lambda: False)
    out = Host()._log_foreign_vpn("vps.example", "")
    assert out == cmds

    ran: list[list[str]] = []
    monkeypatch.setattr("desktop.procutil.is_admin", lambda: True)
    monkeypatch.setattr(
        "desktop.connection.run_route_cmds",
        lambda lines: ran.append(list(lines)) or lines,
    )
    out = Host()._log_foreign_vpn("vps.example", "")
    assert out == []
    assert ran and ran[0] == cmds


def test_tun_owns_default_from_rows() -> None:
    from desktop.tun import tun_owns_default

    ours = [
        "0.0.0.0 128.0.0.0 172.19.0.1 172.19.0.2 1",
        "128.0.0.0 128.0.0.0 172.19.0.1 172.19.0.2 1",
    ]
    assert tun_owns_default(ours) is True
    loop = [
        "0.0.0.0 128.0.0.0 0.0.0.0 127.0.0.1 512",
        "128.0.0.0 128.0.0.0 0.0.0.0 127.0.0.1 512",
    ]
    assert tun_owns_default(loop) is False
    foreign = [
        "0.0.0.0 128.0.0.0 10.13.13.2 10.13.13.2 5",
        "128.0.0.0 128.0.0.0 10.13.13.2 10.13.13.2 5",
        *ours,
    ]
    assert tun_owns_default(foreign) is True
    worse = [
        "0.0.0.0 128.0.0.0 10.13.13.2 10.13.13.2 1",
        "128.0.0.0 128.0.0.0 10.13.13.2 10.13.13.2 1",
        "0.0.0.0 128.0.0.0 172.19.0.1 172.19.0.2 25",
        "128.0.0.0 128.0.0.0 172.19.0.1 172.19.0.2 25",
    ]
    assert tun_owns_default(worse) is False
