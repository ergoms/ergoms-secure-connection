"""Route tokens, analyzer classify, and sing-box / PAC wiring."""

from __future__ import annotations

from unittest.mock import patch

from desktop.config_io import default_config_template, effective_proxy_bypass
from desktop.route_analyzer import analyze_token
from desktop.route_tokens import (
    KIND_DOMAIN,
    KIND_IP,
    KIND_PROCESS,
    KIND_SERVICE,
    KIND_UNKNOWN,
    as_cidr,
    canonical_route_token,
    classify_input,
    host_patterns,
    is_host_pattern,
    is_shared_process,
    parse_list,
    parse_routes,
    parse_token,
    process_matchers,
    serialize_list,
    tokens_from_json,
    tokens_json,
)
from desktop.ui.settings_map import apply_settings_to_cfg, cfg_to_settings, settings_defaults
from lib.pac import build_pac, bypass_to_singbox


def test_parse_legacy_domains_and_globs() -> None:
    tokens = parse_list(["*.local", "github.com", ""])
    assert [t.kind for t in tokens] == [KIND_DOMAIN, KIND_DOMAIN]
    parsed = parse_routes(["*.local", "intranet.corp", "8.8.8.8"])
    assert parsed.suffixes == [".local"]
    assert parsed.domains == ["intranet.corp"]
    assert parsed.ips == ["8.8.8.8/32"]


def test_parse_process_and_service_prefixes() -> None:
    exe = parse_token("chrome.exe")
    assert exe is not None
    assert exe.kind == KIND_PROCESS
    assert exe.serialize() == "exe:chrome.exe"
    svc = parse_token("svc:Spooler")
    assert svc is not None
    assert svc.kind == KIND_SERVICE
    assert svc.label == "Spooler"
    path = parse_token(r"C:\Program Files\App\app.exe")
    assert path is not None
    assert path.kind == KIND_PROCESS


def test_ambiguous_single_label() -> None:
    kind, ambiguous = classify_input("spotify")
    assert kind == KIND_UNKNOWN
    assert ambiguous
    tok = parse_token("spotify")
    assert tok is not None and tok.kind == KIND_DOMAIN
    assert classify_input("localhost") == (KIND_DOMAIN, False)


def test_shared_process_and_matchers() -> None:
    assert is_shared_process("svchost.exe")
    assert process_matchers("svchost.exe") == ([], [])
    names, paths = process_matchers("chrome")
    assert "chrome.exe" in names
    _, only_path = process_matchers(r"C:\Apps\slack.exe")
    assert only_path == [r"C:\Apps\slack.exe"]


def test_host_patterns_skip_exe() -> None:
    assert is_host_pattern("github.com")
    assert is_host_pattern("1.2.3.4")
    assert not is_host_pattern("exe:chrome.exe")
    assert not is_host_pattern("svc:Spooler")
    assert host_patterns(["*.local", "exe:foo.exe", "svc:Bar"]) == ["*.local"]


def test_as_cidr() -> None:
    assert as_cidr("10.0.0.1") == "10.0.0.1/32"
    assert as_cidr("10.0.0.0/8") == "10.0.0.0/8"


def test_bypass_to_singbox_ignores_exe_and_ip() -> None:
    suffixes, domains = bypass_to_singbox(
        ["*.local", "intranet.corp", "exe:chrome.exe", "8.8.8.8", "svc:Spooler"]
    )
    assert ".local" in suffixes
    assert "intranet.corp" in domains
    assert "chrome.exe" not in domains
    assert "8.8.8.8" not in domains


def test_pac_skips_process_tokens() -> None:
    body = build_pac(
        1088,
        "github",
        ["github.com", "exe:chrome.exe", "svc:Spooler"],
        ["*.local", "exe:slack.exe"],
        "",
        "direct",
    ).decode("utf-8")
    assert "github.com" in body
    assert "exe:" not in body
    assert "slack.exe" not in body
    assert "*.local" in body


def test_settings_route_json_roundtrip() -> None:
    cfg = default_config_template()
    cfg["proxy_bypass"] = ["*.local", "exe:slack.exe"]
    cfg["blocked_hosts"] = ["github.com", "1.1.1.1"]
    settings = cfg_to_settings(cfg)
    assert "routeDirect" in settings_defaults()
    assert tokens_from_json(settings["routeDirect"]) == ["*.local", "exe:slack.exe"]
    assert tokens_from_json(settings["routeVpn"]) == ["github.com", "1.1.1.1"]

    def get(key: str) -> object:
        return settings[key]

    out = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert "exe:slack.exe" in out["proxy_bypass"]
    assert "1.1.1.1" in out["blocked_hosts"]
    assert out["direct_ru"] is False
    settings["directRu"] = True
    ru = apply_settings_to_cfg(default_config_template(), get, corporate=False)
    assert ru["direct_ru"] is True
    assert "*.ru" not in ru["proxy_bypass"]


def test_effective_proxy_bypass_direct_ru_default_off() -> None:
    cfg = default_config_template()
    assert effective_proxy_bypass(cfg) == list(cfg["proxy_bypass"])
    cfg["direct_ru"] = True
    assert effective_proxy_bypass(cfg)[-1] == "*.ru"
    cfg["proxy_bypass"] = ["*.local", "*.ru"]
    assert effective_proxy_bypass(cfg) == ["*.local", "*.ru"]


def test_tokens_json_empty() -> None:
    assert tokens_from_json("[]") == []
    assert tokens_from_json(None) == []
    assert tokens_json(["a", " b "]) == tokens_json(["a", "b"])


def test_https_url_becomes_domain() -> None:
    tok = parse_token("https://github.com/org/repo?tab=readme")
    assert tok is not None
    assert tok.kind == KIND_DOMAIN
    assert tok.value == "github.com"
    assert canonical_route_token("HTTPS://WWW.Example.com:443/path") == "www.example.com"
    assert classify_input("https://github.com/foo") == (KIND_DOMAIN, False)
    assert parse_token("http://10.0.0.8/health") is not None
    assert parse_token("http://10.0.0.8/health").kind == KIND_IP
    assert parse_token("http://10.0.0.8/health").value == "10.0.0.8"
    parsed = parse_routes(["https://api.github.com/user"])
    assert parsed.domains == ["api.github.com"]
    assert host_patterns(["https://github.com/org/repo"]) == ["github.com"]


def test_analyze_https_url_without_dns() -> None:
    with (
        patch("desktop.route_analyzer.resolve_ips", return_value=["1.2.3.4"]),
        patch("desktop.route_analyzer.reverse_name", return_value="dns.google"),
    ):
        domain = analyze_token("example.com")
        assert domain["kind"] == KIND_DOMAIN
        assert domain["ips"] == ["1.2.3.4"]
        ip = analyze_token("8.8.8.8")
        assert ip["kind"] == KIND_IP
        assert ip["domains"] == ["dns.google"]
    lonely = analyze_token("spotify")
    assert lonely["ambiguous"]
    with (
        patch("desktop.route_analyzer.resolve_ips", return_value=["1.2.3.4"]),
        patch("desktop.route_analyzer.reverse_name", return_value="dns.google"),
    ):
        url = analyze_token("https://example.com/path")
        assert url["kind"] == KIND_DOMAIN
        assert url["token"] == "example.com"
        assert url["ips"] == ["1.2.3.4"]


def test_route_rules_vpn_before_direct() -> None:
    from desktop.singbox_mode import _build_route_rules

    with patch("desktop.singbox.manager.resolve_host", return_value="203.0.113.10"):
        with patch("desktop.singbox.manager._direct_python_paths", return_value=[]):
            rules, suffixes, domains = _build_route_rules(
                server_host="vps.example",
                exclude_ips=["203.0.113.10"],
                vpn_port=443,
                vps_proxy_ports=[22],
                bypass_hosts=["*.corp", "exe:slack.exe"],
                vpn_hosts=["secret.corp", "exe:chrome.exe"],
                via_proxy=False,
            )
    vpn_idx = next(
        i
        for i, r in enumerate(rules)
        if r.get("outbound") == "proxy" and "secret.corp" in (r.get("domain") or [])
    )
    direct_idx = next(
        i
        for i, r in enumerate(rules)
        if r.get("outbound") == "direct" and ".corp" in (r.get("domain_suffix") or [])
    )
    assert vpn_idx < direct_idx
    assert any(
        r.get("outbound") == "proxy" and "chrome.exe" in (r.get("process_name") or [])
        for r in rules
    )
    assert any(
        r.get("outbound") == "direct" and "slack.exe" in (r.get("process_name") or [])
        for r in rules
    )
    assert ".corp" in suffixes or any(
        ".corp" in (r.get("domain_suffix") or []) for r in rules
    )
    assert "intranet.corp" not in domains


def test_proxy_override_skips_exe() -> None:
    from desktop.win_proxy import proxy_override_list

    text = proxy_override_list(["*.local", "exe:chrome.exe", "svc:Spooler"])
    assert "*.local" in text
    assert "exe:" not in text
    assert "Spooler" not in text


def test_serialize_roundtrip() -> None:
    tokens = parse_list(["github.com", "exe:app.exe", "svc:Spooler"])
    raw = serialize_list(tokens)
    again = parse_list(raw)
    assert [t.kind for t in again] == [KIND_DOMAIN, KIND_PROCESS, KIND_SERVICE]


def test_analyze_process_shows_program_name_not_pid() -> None:
    from desktop.route_analyzer import analyze_process

    with patch("desktop.route_analyzer.collect_peers", return_value=[]), patch(
        "desktop.route_analyzer._enrich_peers", return_value=[]
    ):
        payload = analyze_process(
            pid=1264,
            name="MsSense.exe",
            path=r"C:\Program Files\Windows Defender Advanced Threat Protection\MsSense.exe",
        )
    assert payload["label"] == "MsSense.exe"
    assert "pid" not in payload["label"].lower()
    assert payload["token"].endswith("MsSense.exe")


def test_analyze_spec_json_process(monkeypatch) -> None:
    from desktop.ui.bridge import _analyze_spec

    captured: dict[str, object] = {}

    def fake_analyze_process(**kwargs):
        captured.update(kwargs)
        return {"kind": "process", "label": kwargs.get("display") or kwargs.get("name"), "peers": []}

    monkeypatch.setattr("desktop.ui.bridge.analyze_process", fake_analyze_process)
    _analyze_spec(
        '{"kind":"process","pid":1264,"name":"MsSense.exe","path":"C:\\\\MsSense.exe","display":"MsSense.exe"}'
    )
    assert captured["pid"] == 1264
    assert captured["name"] == "MsSense.exe"
    assert "pid" not in str(captured.get("display") or "").lower()
