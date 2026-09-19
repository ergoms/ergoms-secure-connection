"""Pin current behavior of functions later phases will split or replace."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from desktop.config.model import default_amneziawg_block
from desktop.config_io import (
    default_config_template,
    ensure_config_defaults,
    normalize_dial,
)
from desktop.kill_switch import _cmds_linux_install, _cmds_win_install
from desktop.singbox_mode import (
    SingboxModeManager,
    amneziawg_opts,
    effective_tun_mtu,
    require_transport,
    rustdesk_hairpin_rules,
    underlay_bind_target,
    underlay_keep_hosts,
)
from desktop.ui.settings_map import apply_settings_to_cfg, cfg_to_settings, settings_defaults
from lib.pac import build_pac, bypass_to_singbox


def _transport(
    *,
    dial: str,
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
    awg: bool = False,
    rustdesk: bool = True,
) -> dict[str, Any]:
    mgr = SingboxModeManager(
        var_dir=__import__("pathlib").Path("var"),
        tools_dir=__import__("pathlib").Path("tools"),
        logs_dir=__import__("pathlib").Path("logs"),
        log=lambda _m: None,
    )
    def _resolve(host: str) -> str:
        name = (host or "").split(":")[0].strip()
        if name == "192.0.2.10":
            return "192.0.2.10"
        return "203.0.113.10"

    with (
        patch("desktop.singbox.manager.resolve_host", side_effect=_resolve),
        patch("desktop.singbox.manager.detect_bind_interface", return_value=""),
        patch("desktop.singbox.manager.iface_ipv4s", return_value=[]),
        patch("desktop.singbox.manager._direct_python_paths", return_value=[]),
    ):
        return mgr.build_config(
            server_host="vps.example",
            transport=_transport(dial=dial, awg=awg),
            corporate_proxy="192.0.2.10:3128" if office else "",
            socks_port=1080,
            http_port=1088,
            enable_tun=enable_tun,
            bypass_hosts=["*.local", "*.lan"],
            mtu=1400,
            vps_proxy_ports=[22],
            kill_switch=False,
            rustdesk=rustdesk,
        )


def _rustdesk_rules(box: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in box["route"]["rules"]:
        port = rule.get("port")
        ports = port if isinstance(port, list) else [port] if port is not None else []
        if 21117 in ports and not rule.get("override_address"):
            out.append(rule)
    return out


def test_require_transport_awg_without_reality_uuid() -> None:
    cfg = {
        "transport": {
            "dial": "amneziawg",
            "type": "vless-reality",
            "uuid": "",
            "public_key": "",
            "amneziawg": {
                "private_key": "awg-priv",
                "peer_public_key": "awg-pub",
            },
        }
    }
    out = require_transport(cfg)
    assert out["dial"] == "amneziawg"
    assert out["amneziawg"]["private_key"] == "awg-priv"


def test_rustdesk_hairpin_ip_and_hostname() -> None:
    with patch("desktop.singbox.manager.resolve_host", return_value="203.0.113.10"):
        rules = rustdesk_hairpin_rules("vps.example")
    assert any(
        "203.0.113.10/32" in (r.get("ip_cidr") or [])
        and r.get("port") == [21117]
        and not r.get("override_address")
        for r in rules
    )
    assert any(
        "203.0.113.10/32" in (r.get("ip_cidr") or [])
        and 21116 in (r.get("port") or [])
        and r.get("override_address") == "127.0.0.1"
        for r in rules
    )
    assert any(
        "vps.example" in (r.get("domain") or []) and r.get("outbound") == "proxy"
        for r in rules
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
    assert not any(
        r.get("network") == "udp" and r.get("port") == 443 and r.get("action") == "reject"
        for r in (box.get("route") or {}).get("rules") or []
    )


def test_build_config_office_vless_via_squid() -> None:
    box = _build(dial="vless-reality", office=True, enable_tun=False)
    types = [ob["type"] for ob in box["outbounds"]]
    tags = {ob["tag"]: ob for ob in box["outbounds"]}
    assert "http" in types
    assert tags["squid"]["server"] == "192.0.2.10"
    assert tags["proxy"]["type"] == "vless"
    assert tags["proxy"]["detour"] == "squid"
    assert box["route"]["final"] == "proxy"
    assert box["dns"]["strategy"] == "ipv4_only"
    assert any(
        r.get("network") == "udp" and r.get("port") == 443 and r.get("action") == "reject"
        for r in box["route"]["rules"]
    )
    rules = box["route"]["rules"]
    priv = next(i for i, r in enumerate(rules) if r.get("ip_is_private"))
    hijack = next(i for i, r in enumerate(rules) if r.get("action") == "hijack-dns")
    assert priv < hijack


def test_office_tun_mtu_and_sniff_are_ssh_friendly() -> None:
    box = _build(dial="vless-reality", office=True, enable_tun=True)
    tun = next(ib for ib in box["inbounds"] if ib["type"] == "tun")
    assert tun["mtu"] == 1280
    sniff = [r for r in box["route"]["rules"] if r.get("action") == "sniff"]
    assert sniff
    assert all(r.get("timeout") == "100ms" for r in sniff)
    tun_sniff = [r for r in sniff if r.get("inbound") == ["tun-in"]]
    assert tun_sniff and all(r.get("network") == "tcp" for r in tun_sniff)


def test_effective_tun_mtu_caps_fragments() -> None:
    assert effective_tun_mtu(1500) == 1400
    assert effective_tun_mtu(1500, via_office_proxy=True) == 1280
    assert effective_tun_mtu(1400, awg_mtu=1280) == 1280


def test_build_config_home_vless_with_tun() -> None:
    box = _build(dial="vless-reality", office=False, enable_tun=True)
    tun = next(ib for ib in box["inbounds"] if ib["type"] == "tun")
    assert tun["address"] == ["172.19.0.1/30"]
    assert tun["mtu"] == 1400
    assert tun["auto_route"] is False or isinstance(tun["auto_route"], bool)
    types = [ob["type"] for ob in box["outbounds"]]
    assert "vless" in types
    assert "http" not in types
    rustdesk = _rustdesk_rules(box)
    assert rustdesk
    sniff_idx = next(
        i
        for i, r in enumerate(box["route"]["rules"])
        if r.get("action") == "sniff" and r.get("inbound") == ["tun-in"]
    )
    rd_idx = next(
        i
        for i, r in enumerate(box["route"]["rules"])
        if 21117 in (
            r.get("port") if isinstance(r.get("port"), list) else [r.get("port")]
        )
        and not r.get("override_address")
    )
    assert rd_idx < sniff_idx


def _rustdesk_tun_lan_reject(box: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in box["route"]["rules"]:
        if rule.get("action") != "reject":
            continue
        cidrs = rule.get("ip_cidr") or []
        names = [str(n).lower() for n in (rule.get("process_name") or [])]
        if "172.19.0.0/16" in cidrs and "rustdesk.exe" in names:
            out.append(rule)
    return out


def test_rustdesk_tun_lan_rejected_before_sniff() -> None:
    """Same 172.19.0.1 on every PC makes RustDesk punch itself; force real relay."""
    for office in (False, True):
        box = _build(dial="vless-reality", office=office, enable_tun=True)
        reject = _rustdesk_tun_lan_reject(box)
        assert reject
        rules = box["route"]["rules"]
        reject_idx = next(
            i
            for i, r in enumerate(rules)
            if r.get("action") == "reject"
            and "172.19.0.0/16" in (r.get("ip_cidr") or [])
        )
        sniff_idx = next(
            i
            for i, r in enumerate(rules)
            if r.get("action") == "sniff" and r.get("inbound") == ["tun-in"]
        )
        rd_idx = next(
            i
            for i, r in enumerate(rules)
            if 21117
            in (r.get("port") if isinstance(r.get("port"), list) else [r.get("port")])
            and not r.get("override_address")
        )
        priv_idx = next(i for i, r in enumerate(rules) if r.get("ip_is_private"))
        assert rd_idx < reject_idx < sniff_idx
        assert reject_idx < priv_idx


def test_rustdesk_rules_omitted_when_disabled() -> None:
    box = _build(dial="vless-reality", office=True, enable_tun=True, rustdesk=False)
    assert not _rustdesk_rules(box)
    assert not _rustdesk_tun_lan_reject(box)


def _ssh_port_rules(box: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule in box["route"]["rules"]:
        port = rule.get("port")
        if port == 22 or (isinstance(port, list) and 22 in port):
            out.append(rule)
    return out


def test_home_ssh_to_vps_is_direct() -> None:
    box = _build(dial="vless-reality", office=False, enable_tun=True)
    ssh = _ssh_port_rules(box)
    assert any(
        r.get("inbound") == ["socks-in", "http-in"] and r.get("outbound") == "proxy"
        for r in ssh
    )
    assert not any(r.get("outbound") == "proxy" and not r.get("inbound") for r in ssh)
    assert any(
        "203.0.113.10/32" in (r.get("ip_cidr") or []) and r.get("outbound") == "direct"
        for r in box["route"]["rules"]
    )


def test_office_ssh_to_vps_via_proxy() -> None:
    box = _build(dial="vless-reality", office=True, enable_tun=True)
    tun = next(ib for ib in box["inbounds"] if ib["type"] == "tun")
    excluded = tun.get("route_exclude_address") or []
    assert "203.0.113.10/32" not in excluded
    assert "192.0.2.10/32" in excluded
    hairpin = [
        r
        for r in box["route"]["rules"]
        if "203.0.113.10/32" in (r.get("ip_cidr") or [])
        and r.get("outbound") == "proxy"
        and r.get("override_address") == "127.0.0.1"
        and not r.get("port")
        and not r.get("inbound")
    ]
    assert hairpin
    assert _rustdesk_rules(box)


def test_user_ssh_and_remote_ssh_are_not_rejected() -> None:
    """Outgoing OpenSSH / Cursor Remote-SSH must stay usable under TUN."""
    for office in (False, True):
        box = _build(dial="vless-reality", office=office, enable_tun=True)
        for rule in box["route"]["rules"]:
            if rule.get("action") != "reject":
                continue
            names = [str(n).lower() for n in (rule.get("process_name") or [])]
            assert "ssh.exe" not in names and "ssh" not in names
            port = rule.get("port")
            ports = port if isinstance(port, list) else [port] if port is not None else []
            if rule.get("network") == "udp":
                continue
            assert 22 not in ports


def test_underlay_keep_hosts_office_vless_pins_only_squid() -> None:
    assert underlay_keep_hosts("vps.example", "192.0.2.10:3128") == ["192.0.2.10"]
    assert underlay_keep_hosts("vps.example", "") == ["vps.example"]
    assert underlay_keep_hosts(
        "vps.example", "192.0.2.10:3128", udp_dial=True
    ) == ["192.0.2.10", "vps.example"]


def test_underlay_forget_hosts_office_drops_vps_pin() -> None:
    from desktop.singbox_mode import underlay_forget_hosts

    assert underlay_forget_hosts("vps.example", "192.0.2.10:3128") == ["vps.example"]
    assert underlay_forget_hosts("vps.example", "") == []
    assert underlay_forget_hosts("vps.example", "192.0.2.10:3128", udp_dial=True) == []


def test_win_kill_switch_install_forgets_stale_vps_pin(monkeypatch: Any) -> None:
    from desktop.killswitch import _impl as ks

    monkeypatch.setattr(ks.sys, "platform", "win32")
    assert ks.forget_host_commands(["203.0.113.10"], ["10.16.0.8"]) == [
        "route delete 203.0.113.10 mask 255.255.255.255"
    ]
    assert ks.forget_host_commands(["10.16.0.8"], ["10.16.0.8"]) == []
    cmds = ks.pin_commands(
        ["10.16.0.8"], gw="10.193.0.1", if_idx=10, forget=["203.0.113.10"]
    )
    assert cmds[0] == "route delete 203.0.113.10 mask 255.255.255.255"
    assert any("route add 10.16.0.8" in c for c in cmds)


def test_underlay_bind_target_awg_uses_vps_not_squid() -> None:
    assert underlay_bind_target(
        "203.0.113.10",
        ["192.0.2.10", "203.0.113.10"],
        squid_host="192.0.2.10",
        udp_dial=True,
    ) == "203.0.113.10"
    assert underlay_bind_target(
        "203.0.113.10",
        ["192.0.2.10"],
        squid_host="192.0.2.10",
        udp_dial=False,
    ) == "192.0.2.10"


def _office_awg_hairpin(box: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        r
        for r in (box.get("route") or {}).get("rules") or []
        if "203.0.113.10/32" in (r.get("ip_cidr") or [])
        and r.get("outbound") == "proxy"
        and r.get("override_address") == "127.0.0.1"
        and not r.get("port")
        and not r.get("inbound")
    ]


def test_build_config_office_awg_with_tun() -> None:
    box = _build(dial="amneziawg", office=True, awg=True, enable_tun=True)
    assert "endpoints" in box
    assert box["endpoints"][0]["type"] == "awg"
    assert box["endpoints"][0]["peers"][0]["address"] == "203.0.113.10"
    assert any(ib["type"] == "tun" for ib in box["inbounds"])
    assert box["route"]["default_domain_resolver"] == "dns-local"
    tags = [ob.get("tag") for ob in box["outbounds"]]
    types = [ob.get("type") for ob in box["outbounds"]]
    assert tags == ["direct"]
    assert types == ["direct"]
    tun = next(ib for ib in box["inbounds"] if ib["type"] == "tun")
    excluded = tun.get("route_exclude_address") or []
    assert "192.0.2.10/32" in excluded
    assert "203.0.113.10/32" in excluded
    assert not _office_awg_hairpin(box)
    assert _rustdesk_rules(box)
    assert not any(
        r.get("network") == "udp" and r.get("port") == 443 and r.get("action") == "reject"
        for r in box["route"]["rules"]
    )


def test_build_config_office_awg_minimal_without_tun() -> None:
    box = _build(dial="amneziawg", office=True, awg=True, enable_tun=False)
    inbound_types = [ib["type"] for ib in box["inbounds"]]
    assert "tun" not in inbound_types
    assert "socks" in inbound_types
    assert "http" in inbound_types
    assert box["endpoints"][0]["type"] == "awg"
    assert box["endpoints"][0]["peers"][0]["address"] == "203.0.113.10"
    assert [ob.get("tag") for ob in box["outbounds"]] == ["direct"]
    assert not any(
        r.get("network") == "udp" and r.get("port") == 443 and r.get("action") == "reject"
        for r in (box.get("route") or {}).get("rules") or []
    )
    assert not _office_awg_hairpin(box)


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
            "transport": {"dial": "hy2"},
        }
    )
    assert out["server"]["host"] == "legacy.example"
    assert "ssh" not in out
    assert "worker_base_url" not in out
    assert out["tun"]["enabled"] is False
    assert out["socks_scope"] == "full"
    assert out["transport"]["dial"] == "amneziawg"
    assert "hysteria2" not in out["transport"]
    assert 1280 <= out["tun"]["mtu"] <= 1400
    assert out["transport"]["amneziawg"]["address"] == "10.66.66.2/32"


def test_ensure_config_defaults_clamps_mtu() -> None:
    out = ensure_config_defaults({"tun": {"mtu": 9000}, "transport": {"amneziawg": {"mtu": 500}}})
    assert out["tun"]["mtu"] == 1400
    assert out["transport"]["amneziawg"]["mtu"] == 1280


def test_settings_defaults_match_template_ports() -> None:
    tmpl = default_config_template()
    defaults = settings_defaults()
    assert defaults["httpBridgePort"] == str(tmpl["http_bridge_port"])
    assert defaults["serverSocks"] == str(tmpl["server"]["local_socks_port"])
    assert defaults["trDial"] == normalize_dial(tmpl["transport"]["dial"])
    assert defaults["awgPort"] == str(tmpl["transport"]["amneziawg"]["port"])


def test_settings_map_roundtrip_preserves_transport() -> None:
    cfg = default_config_template()
    cfg["server"]["host"] = "vps.example"
    cfg["transport"]["uuid"] = "u-1"
    cfg["transport"]["dial"] = "amneziawg"
    settings = cfg_to_settings(cfg)

    def get(key: str) -> object:
        return settings[key]

    out = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert out["rustdesk"] is False
    settings["rustdesk"] = True
    on = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert on["rustdesk"] is True
    assert out["server"]["host"] == "vps.example"
    assert out["transport"]["uuid"] == "u-1"
    assert out["transport"]["dial"] == "amneziawg"
    assert "hysteria2" not in out["transport"]


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
    assert "ip -6 route replace unreachable ::/1 metric 512" in cmds
    assert "ip -6 route replace ::/1 dev lo metric 512" not in cmds


def test_build_pac_full_and_github() -> None:
    full = build_pac(1088, "full", [], ["*.local"], "", "direct").decode()
    assert "PROXY 127.0.0.1:1088" in full
    assert "ERGOMS" in full
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
    from desktop.config_io import persistable_config

    example = json.loads(
        (Path("config") / "config.example.json").read_text(encoding="utf-8")
    )
    assert example == persistable_config(AppConfig().to_dict())
    assert "amneziawg" not in example["transport"]
    assert "hysteria2" not in example["transport"]


def test_bypass_to_singbox_splits_suffix_and_domain() -> None:
    suffixes, domains = bypass_to_singbox(["*.local", "intranet.corp", "", "*.lan"])
    assert ".local" in suffixes
    assert ".lan" in suffixes
    assert "intranet.corp" in domains


def _present(**st: Any):
    from desktop.services.status import present_status

    return present_status(st, config_ready=bool(st.pop("_ready", True)))


def test_present_status_all_branches() -> None:
    from desktop.services.status import C_ACCENT, C_DANGER, C_MUTED, C_OK, C_WARN

    connecting = _present(
        connecting=True, singbox_running=True, tun_wanted=True, tun_ready=False
    )
    assert (connecting.title, connecting.power_text, connecting.color) == (
        "Подключение…",
        "Отключить",
        C_ACCENT,
    )
    assert connecting.can_reconnect is False

    tun_not_ready = _present(
        singbox_running=True, tun_wanted=True, tun_ready=False, tun_running=True
    )
    assert tun_not_ready.title == "Подключение…"

    need_awg = _present(
        singbox_running=True,
        socks_up=True,
        exit_probe_error="timeout",
        exit_probe_hint="need-awg",
    )
    assert need_awg.title == "Нет выхода"
    assert "AWG" in need_awg.subtitle
    assert need_awg.can_reconnect is True
    assert need_awg.toast == need_awg.subtitle
    assert need_awg.color == C_DANGER

    udp = _present(
        singbox_running=True,
        socks_up=True,
        exit_probe_error="timeout",
        exit_probe_hint="udp-timeout",
    )
    assert "конфиг" in udp.subtitle
    assert udp.can_reconnect is True

    generic = _present(
        singbox_running=True, socks_up=True, exit_probe_error="https fail"
    )
    assert generic.subtitle == "Переподключите — защита при обрыве останется"

    protected = _present(
        singbox_running=True, tun_running=True, socks_up=True, tun_ready=True
    )
    assert (protected.title, protected.color, protected.can_reconnect) == (
        "Защищено",
        C_ACCENT,
        False,
    )
    assert protected.tun_button_text == "TUN выкл"

    crash = _present(singbox_running=True, socks_up=False)
    assert crash.title == "Сбой"
    assert crash.can_reconnect is True
    assert crash.power_text == "Переподключить"
    assert crash.color == C_DANGER

    socks_only = _present(singbox_running=True, socks_up=True, tun_running=False)
    assert (socks_only.title, socks_only.color) == ("Подключено", C_OK)

    tun_only = _present(tun_running=True, singbox_running=False)
    assert (tun_only.title, tun_only.color) == ("Подключено", C_WARN)

    sealed = _present(kill_switch_applied=True)
    assert sealed.title == "Нет сети"
    assert sealed.can_reconnect is True
    assert sealed.power_text == "Переподключить"
    assert sealed.color == C_WARN

    no_cfg = _present(_ready=False)
    assert (no_cfg.title, no_cfg.power_text, no_cfg.color) == (
        "Нет конфига",
        "Подключить",
        C_MUTED,
    )

    idle = _present()
    assert (idle.title, idle.power_text, idle.tun_button_text) == (
        "Отключено",
        "Подключить",
        "TUN вкл",
    )
    assert idle.signature


def implicit_connect_plan(
    *,
    office: bool,
    dial: str,
    tun_enabled: bool,
    kill_switch: bool,
    platform: str = "win32",
) -> dict[str, Any]:
    """Pin start_singbox_mode heuristics before they become ConnectPlan fields."""
    awg = dial == "amneziawg"
    enable_tun = tun_enabled or kill_switch
    udp_dial = awg
    defer_win = platform == "win32" and (not office or udp_dial)
    defer_win_ks = bool(defer_win and kill_switch and udp_dial)
    return {
        "dial": dial,
        "awg": awg,
        "tun_wanted": enable_tun,
        "tun_deferred": False,
        "start_tun": enable_tun,
        "ks_wanted": kill_switch,
        "ks_deferred": defer_win_ks,
        "start_ks": kill_switch and not defer_win_ks,
    }


def test_implicit_connect_plan_six_combos() -> None:
    office_vless_tun = implicit_connect_plan(
        office=True, dial="vless-reality", tun_enabled=True, kill_switch=True
    )
    assert office_vless_tun["start_tun"] is True
    assert office_vless_tun["start_ks"] is True
    assert office_vless_tun["tun_deferred"] is False
    assert office_vless_tun["ks_deferred"] is False

    office_vless_off = implicit_connect_plan(
        office=True, dial="vless-reality", tun_enabled=False, kill_switch=False
    )
    assert office_vless_off["start_tun"] is False
    assert office_vless_off["start_ks"] is False
    assert office_vless_off["tun_wanted"] is False

    home_awg_tun = implicit_connect_plan(
        office=False, dial="amneziawg", tun_enabled=True, kill_switch=True
    )
    assert home_awg_tun["tun_deferred"] is False
    assert home_awg_tun["ks_deferred"] is True
    assert home_awg_tun["start_tun"] is True
    assert home_awg_tun["start_ks"] is False
    assert home_awg_tun["tun_wanted"] is True

    home_awg_off = implicit_connect_plan(
        office=False, dial="amneziawg", tun_enabled=False, kill_switch=False
    )
    assert home_awg_off["tun_deferred"] is False
    assert home_awg_off["start_tun"] is False

    office_awg_tun = implicit_connect_plan(
        office=True, dial="amneziawg", tun_enabled=True, kill_switch=True
    )
    assert office_awg_tun["tun_deferred"] is False
    assert office_awg_tun["ks_deferred"] is True
    assert office_awg_tun["start_tun"] is True
    assert office_awg_tun["start_ks"] is False

    home_vless_tun = implicit_connect_plan(
        office=False, dial="vless-reality", tun_enabled=True, kill_switch=True
    )
    assert home_vless_tun["start_tun"] is True
    assert home_vless_tun["start_ks"] is True
    assert home_vless_tun["ks_deferred"] is False
    assert home_vless_tun["tun_deferred"] is False


def test_linux_kill_switch_install_and_remove_are_stable() -> None:
    from desktop.kill_switch import _cmds_linux_remove

    install = _cmds_linux_install(["203.0.113.10"], "10.193.0.1", blackhole=False)
    assert "ip route del 0.0.0.0/1 dev lo" in install
    assert "ip route replace 203.0.113.10/32 via 10.193.0.1" in install
    assert "ip -6 route replace unreachable ::/1 metric 512" in install
    assert "ip -6 route replace ::/1 dev lo metric 512" not in install
    remove = _cmds_linux_remove(["203.0.113.10"], "10.193.0.1")
    assert "ip route del 203.0.113.10/32" in remove
    assert "ip -6 route del ::/1" in remove
    assert "ip -6 route del ::/1 dev lo" in remove


def test_win_kill_switch_remove_clears_split_and_pins() -> None:
    from desktop.kill_switch import _cmds_win_remove

    cmds = _cmds_win_remove(["203.0.113.10"], "10.193.0.1")
    joined = "\n".join(cmds)
    assert "route delete 0.0.0.0 mask 128.0.0.0 127.0.0.1" in joined
    assert "route delete 203.0.113.10 mask 255.255.255.255" in joined
    assert "netsh interface ipv6 delete route ::/1" in joined


def test_tun_readiness_linux_skips_adapter_wait(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from pathlib import Path

    from desktop.singbox_mode import SingboxModeManager

    mgr = SingboxModeManager(Path(tmp_path), Path(tmp_path), Path(tmp_path), log=lambda _m: None)
    monkeypatch.setattr("desktop.singbox.readiness.sys.platform", "linux")
    monkeypatch.setattr(mgr, "tail_log", lambda n=40: [])
    assert mgr._tun_inbound_ready() is True
    monkeypatch.setattr("desktop.singbox.readiness.port_open", lambda *_a, **_k: True)
    monkeypatch.setattr("desktop.singbox.readiness.procutil.pid_alive", lambda _pid: True)
    assert mgr._wait_ready(1080, 1088, enable_tun=True, pid=1, timeout=0.2) is True


def test_tun_readiness_socks_without_tun(tmp_path: Any, monkeypatch: Any) -> None:
    from pathlib import Path

    from desktop.singbox_mode import SingboxModeManager

    mgr = SingboxModeManager(Path(tmp_path), Path(tmp_path), Path(tmp_path), log=lambda _m: None)
    monkeypatch.setattr("desktop.singbox.readiness.port_open", lambda *_a, **_k: True)
    monkeypatch.setattr("desktop.singbox.readiness.procutil.pid_alive", lambda _pid: True)
    assert mgr._wait_ready(1080, 1088, enable_tun=False, pid=1, timeout=0.2) is True
