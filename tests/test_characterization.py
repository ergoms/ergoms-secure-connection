"""Pin current behavior of functions later phases will split or replace."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from desktop.config_io import (
    default_amneziawg_block,
    default_config_template,
    ensure_config_defaults,
    normalize_dial,
)
from desktop.kill_switch import _cmds_linux_install, _cmds_win_install
from desktop.singbox_mode import SingboxModeManager, amneziawg_opts, hysteria2_opts
from desktop.ui.settings_map import apply_settings_to_cfg, cfg_to_settings, settings_defaults
from lib.pac import build_pac, bypass_to_singbox


def _transport(
    *,
    dial: str,
    hy2: bool = False,
    awg: bool = False,
) -> dict[str, Any]:
    tr: dict[str, Any] = {
        "type": "vless-reality",
        "dial": dial,
        "uuid": "11111111-1111-1111-1111-111111111111",
        "public_key": "pub-key-from-vps",
        "short_id": "abcd",
        "server_name": "www.cloudflare.com",
        "port": 443,
        "hysteria2": {
            "password": "hy2-secret" if hy2 else "",
            "port": 8443,
            "server_name": "www.microsoft.com",
            "obfs_password": "",
            "insecure": True,
        },
        "amneziawg": {
            **default_amneziawg_block(),
            "private_key": "awg-priv" if awg else "",
            "peer_public_key": "awg-pub" if awg else "",
        },
    }
    return tr


def _build(
    *,
    dial: str,
    office: bool,
    enable_tun: bool = False,
    hy2: bool = False,
    awg: bool = False,
) -> dict[str, Any]:
    mgr = SingboxModeManager(
        var_dir=__import__("pathlib").Path("var"),
        tools_dir=__import__("pathlib").Path("tools"),
        logs_dir=__import__("pathlib").Path("logs"),
        log=lambda _m: None,
    )
    with (
        patch("desktop.singbox_mode.resolve_host", return_value="203.0.113.10"),
        patch("desktop.singbox_mode.detect_bind_interface", return_value=""),
        patch("desktop.singbox_mode.iface_ipv4s", return_value=[]),
        patch("desktop.singbox_mode._direct_python_paths", return_value=[]),
    ):
        return mgr.build_config(
            server_host="vps.example",
            transport=_transport(dial=dial, hy2=hy2, awg=awg),
            corporate_proxy="192.0.2.10:3128" if office else "",
            socks_port=1080,
            http_port=1088,
            enable_tun=enable_tun,
            bypass_hosts=["*.local", "*.lan"],
            mtu=1400,
            vps_proxy_ports=[22],
            kill_switch=False,
        )


def test_build_config_home_awg_minimal_without_tun() -> None:
    box = _build(dial="amneziawg", office=False, awg=True, enable_tun=False)
    tags = [ob.get("tag") for ob in box["outbounds"]]
    assert "endpoints" in box
    assert box["endpoints"][0]["type"] == "awg"
    assert "proxy" not in tags or "direct" in tags
    inbound_types = [ib["type"] for ib in box["inbounds"]]
    assert "tun" not in inbound_types
    assert "socks" in inbound_types
    assert "http" in inbound_types


def test_build_config_home_hy2_minimal_without_tun() -> None:
    box = _build(dial="hysteria2", office=False, hy2=True, enable_tun=False)
    types = [ob["type"] for ob in box["outbounds"]]
    assert "hysteria2" in types
    assert "tun" not in [ib["type"] for ib in box["inbounds"]]


def test_build_config_office_vless_via_squid() -> None:
    box = _build(dial="vless-reality", office=True, enable_tun=False)
    types = [ob["type"] for ob in box["outbounds"]]
    tags = {ob["tag"]: ob for ob in box["outbounds"]}
    assert "http" in types
    assert tags["squid"]["server"] == "192.0.2.10"
    assert tags["proxy"]["type"] == "vless"
    assert tags["proxy"]["detour"] == "squid"
    assert box["route"]["final"] == "proxy"


def test_build_config_home_vless_with_tun() -> None:
    box = _build(dial="vless-reality", office=False, enable_tun=True)
    tun = next(ib for ib in box["inbounds"] if ib["type"] == "tun")
    assert tun["address"] == ["172.19.0.1/30"]
    assert tun["mtu"] == 1400
    assert tun["auto_route"] is False or isinstance(tun["auto_route"], bool)
    types = [ob["type"] for ob in box["outbounds"]]
    assert "vless" in types
    assert "http" not in types


def test_build_config_office_awg_with_tun() -> None:
    box = _build(dial="amneziawg", office=True, awg=True, enable_tun=True)
    assert "endpoints" in box
    assert box["endpoints"][0]["type"] == "awg"
    assert any(ib["type"] == "tun" for ib in box["inbounds"])
    assert box["route"]["default_domain_resolver"] == "dns-local"


def test_hysteria2_opts_requires_password() -> None:
    assert hysteria2_opts({"hysteria2": {"password": ""}}) is None
    assert hysteria2_opts({"hy2_password": "secret"}) is not None
    opts = hysteria2_opts({"hysteria2": {"password": "secret", "port": "8443"}})
    assert opts is not None
    assert opts["port"] == 8443
    assert opts["server_name"] == "www.microsoft.com"


def test_amneziawg_opts_requires_keys() -> None:
    assert amneziawg_opts({"amneziawg": {"private_key": "", "peer_public_key": ""}}) is None
    opts = amneziawg_opts(
        {
            "amneziawg": {
                "private_key": "priv",
                "peer_public_key": "pub",
                "port": 51820,
            }
        }
    )
    assert opts is not None
    assert opts["port"] == 51820
    assert opts["address"] == "10.66.66.2/32"


def test_ensure_config_defaults_fills_and_migrates() -> None:
    out = ensure_config_defaults(
        {
            "ssh": {"host": "legacy.example", "port": 22, "local_socks_port": 1080},
            "tun_enabled": False,
            "worker_base_url": "https://gone.example",
            "socks_scope": "system",
            "transport": {"dial": "hy2", "hysteria2": {"port": 0}},
        }
    )
    assert out["server"]["host"] == "legacy.example"
    assert "ssh" not in out
    assert "worker_base_url" not in out
    assert out["tun"]["enabled"] is False
    assert out["socks_scope"] == "full"
    assert out["transport"]["dial"] == "hysteria2"
    assert 1 <= out["transport"]["hysteria2"]["port"] <= 65535
    assert 1280 <= out["tun"]["mtu"] <= 1500
    assert out["transport"]["amneziawg"]["address"] == "10.66.66.2/32"


def test_ensure_config_defaults_clamps_mtu() -> None:
    out = ensure_config_defaults({"tun": {"mtu": 9000}, "transport": {"amneziawg": {"mtu": 500}}})
    assert out["tun"]["mtu"] == 1500
    assert out["transport"]["amneziawg"]["mtu"] == 1280


def test_settings_defaults_match_template_ports() -> None:
    tmpl = default_config_template()
    defaults = settings_defaults()
    assert defaults["httpBridgePort"] == str(tmpl["http_bridge_port"])
    assert defaults["serverSocks"] == str(tmpl["server"]["local_socks_port"])
    assert defaults["trDial"] == normalize_dial(tmpl["transport"]["dial"])
    assert defaults["hy2Port"] == str(tmpl["transport"]["hysteria2"]["port"])
    assert defaults["awgPort"] == str(tmpl["transport"]["amneziawg"]["port"])


def test_settings_map_roundtrip_preserves_transport() -> None:
    cfg = default_config_template()
    cfg["server"]["host"] = "vps.example"
    cfg["transport"]["uuid"] = "u-1"
    cfg["transport"]["dial"] = "hysteria2"
    cfg["transport"]["hysteria2"]["password"] = "hy2-secret"
    settings = cfg_to_settings(cfg)

    def get(key: str) -> object:
        return settings[key]

    out = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert out["server"]["host"] == "vps.example"
    assert out["transport"]["uuid"] == "u-1"
    assert out["transport"]["dial"] == "hysteria2"
    assert out["transport"]["hysteria2"]["password"] == "hy2-secret"


def test_win_kill_switch_install_pins_and_blackholes() -> None:
    cmds = _cmds_win_install(["203.0.113.10"], "10.193.0.1", blackhole=True)
    joined = "\n".join(cmds)
    assert "route add 0.0.0.0 mask 128.0.0.0 0.0.0.0 metric 512" in joined
    assert "route add 128.0.0.0 mask 128.0.0.0 0.0.0.0 metric 512" in joined
    assert "route add 203.0.113.10 mask 255.255.255.255 10.193.0.1 metric 1" in joined
    assert "netsh interface ipv6 add route ::/1" in joined


def test_linux_kill_switch_install_blackhole() -> None:
    cmds = _cmds_linux_install(["203.0.113.10"], "10.193.0.1", blackhole=True)
    assert "ip route replace 0.0.0.0/1 dev lo metric 512" in cmds
    assert "ip route replace 203.0.113.10/32 via 10.193.0.1" in cmds


def test_build_pac_full_and_github() -> None:
    full = build_pac(1088, "full", [], ["*.local"], "", "direct").decode()
    assert "PROXY 127.0.0.1:1088" in full
    assert "shExpMatch(host, \"*.local\")" in full
    assert "return \"DIRECT\"" in full
    github = build_pac(
        1088,
        "github",
        ["github.com"],
        ["*.lan"],
        "192.0.2.10:3128",
        "corporate",
    ).decode()
    assert "PROXY 192.0.2.10:3128" in github
    assert "github.com" in github


def test_example_json_matches_app_config() -> None:
    from pathlib import Path

    from desktop.config.model import AppConfig

    example = json.loads(
        (Path("config") / "config.example.json").read_text(encoding="utf-8")
    )
    assert example == AppConfig().to_dict()


def test_bypass_to_singbox_splits_suffix_and_domain() -> None:
    suffixes, domains = bypass_to_singbox(["*.local", "intranet.corp", "", "*.lan"])
    assert ".local" in suffixes
    assert ".lan" in suffixes
    assert "intranet.corp" in domains
