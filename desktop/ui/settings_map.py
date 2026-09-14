"""QML settings keys ↔ config.json. One mapping for defaults / load / save."""

from __future__ import annotations

from typing import Any, Callable

from desktop.config_io import (
    AWG_DEFAULT_ADDRESS,
    AWG_DEFAULT_MTU,
    AWG_DEFAULT_PORT,
    HY2_DEFAULT_SNI,
    REALITY_DEFAULT_SNI,
    apply_corporate_profile,
    apply_standard_profile,
    assign_filled,
    filled_str,
    infer_corporate,
    normalize_dial,
    normalize_hy2_sni,
)

GetFn = Callable[[str], Any]


def settings_defaults() -> dict[str, Any]:
    return {
        "corporate": False,
        "useProxy": False,
        "socksScope": "full",
        "tunAuto": True,
        "killSwitch": True,
        "gitProxy": False,
        "dockerProxy": False,
        "httpBridgePort": "1088",
        "corporateProxy": "",
        "serverHost": "",
        "serverPort": "443",
        "serverSocks": "1080",
        "proxyBypass": "",
        "proxyBypassVia": "direct",
        "trUuid": "",
        "trPublicKey": "",
        "trShortId": "",
        "trServerName": REALITY_DEFAULT_SNI,
        "trPort": "443",
        "trDial": "amneziawg",
        "hy2Password": "",
        "hy2Port": "8443",
        "hy2ServerName": HY2_DEFAULT_SNI,
        "hy2Obfs": "",
        "awgPrivateKey": "",
        "awgPeerPublicKey": "",
        "awgPresharedKey": "",
        "awgAddress": AWG_DEFAULT_ADDRESS,
        "awgPort": str(AWG_DEFAULT_PORT),
        "awgMtu": str(AWG_DEFAULT_MTU),
        "awgJc": "0",
        "awgJmin": "0",
        "awgJmax": "0",
        "awgS1": "0",
        "awgS2": "0",
        "awgH1": "",
        "awgH2": "",
        "awgH3": "",
        "awgH4": "",
        "reverseSsh": True,
        "reverseSshListen": "2222",
        "reverseSshVpsUser": "root",
        "reverseSshVpsPort": "22",
    }


def cfg_to_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    server = cfg.get("server") or {}
    tun = cfg.get("tun") or {}
    tr = cfg.get("transport") or {}
    bypass = cfg.get("proxy_bypass") or []
    hy = tr.get("hysteria2") if isinstance(tr.get("hysteria2"), dict) else {}
    awg = tr.get("amneziawg") if isinstance(tr.get("amneziawg"), dict) else {}
    rev = cfg.get("reverse_ssh") or {}
    corporate = infer_corporate(cfg)
    out = settings_defaults()
    out.update(
        {
            "corporate": corporate,
            "socksScope": str(cfg.get("socks_scope") or "full"),
            "tunAuto": bool(tun.get("enabled")),
            "killSwitch": bool(cfg.get("kill_switch", True)),
            "gitProxy": bool(cfg.get("git_proxy")),
            "dockerProxy": bool(cfg.get("docker_proxy")),
            "httpBridgePort": str(cfg.get("http_bridge_port") or 1088),
            "useProxy": bool(cfg.get("use_proxy")) or corporate,
            "corporateProxy": str(cfg.get("corporate_proxy") or ""),
            "serverHost": str(server.get("host") or ""),
            "serverPort": str(server.get("port") or 443),
            "serverSocks": str(server.get("local_socks_port") or 1080),
            "proxyBypass": ", ".join(str(x) for x in bypass),
            "proxyBypassVia": str(cfg.get("proxy_bypass_via") or "direct"),
            "trUuid": str(tr.get("uuid") or ""),
            "trPublicKey": str(tr.get("public_key") or ""),
            "trShortId": str(tr.get("short_id") or ""),
            "trServerName": str(tr.get("server_name") or REALITY_DEFAULT_SNI),
            "trPort": str(tr.get("port") or 443),
            "trDial": normalize_dial(tr.get("dial")),
            "hy2Password": str(hy.get("password") or ""),
            "hy2Port": str(hy.get("port") or 8443),
            "hy2ServerName": normalize_hy2_sni(hy.get("server_name")),
            "hy2Obfs": str(hy.get("obfs_password") or ""),
            "awgPrivateKey": str(awg.get("private_key") or ""),
            "awgPeerPublicKey": str(awg.get("peer_public_key") or ""),
            "awgPresharedKey": str(awg.get("pre_shared_key") or ""),
            "awgAddress": str(awg.get("address") or AWG_DEFAULT_ADDRESS),
            "awgPort": str(awg.get("port") or AWG_DEFAULT_PORT),
            "awgMtu": str(awg.get("mtu") or AWG_DEFAULT_MTU),
            "awgJc": str(awg.get("jc") or 0),
            "awgJmin": str(awg.get("jmin") or 0),
            "awgJmax": str(awg.get("jmax") or 0),
            "awgS1": str(awg.get("s1") or 0),
            "awgS2": str(awg.get("s2") or 0),
            "awgH1": str(awg.get("h1") or ""),
            "awgH2": str(awg.get("h2") or ""),
            "awgH3": str(awg.get("h3") or ""),
            "awgH4": str(awg.get("h4") or ""),
            "reverseSsh": bool(rev.get("enabled", True)),
            "reverseSshListen": str(rev.get("listen_port") or 2222),
            "reverseSshVpsUser": str(rev.get("vps_user") or "root"),
            "reverseSshVpsPort": str(rev.get("vps_port") or 22),
        }
    )
    return out


def _as_int(raw: Any, default: int) -> int:
    try:
        return int(str(raw or "").strip() or str(default))
    except ValueError:
        return default


def _set_awg_int(awg: dict[str, Any], key: str, incoming: Any, default: int = 0) -> None:
    """Keep a nonzero disk value when the form still has 0 / empty."""
    val = _as_int(incoming, default)
    cur = awg.get(key)
    if val or not cur:
        awg[key] = val


def apply_settings_to_cfg(
    cfg: dict[str, Any],
    get: GetFn,
    *,
    corporate: bool,
) -> dict[str, Any]:
    prev_bypass = [str(x).strip() for x in (cfg.get("proxy_bypass") or []) if str(x).strip()]
    prev_proxy = filled_str(cfg.get("corporate_proxy"))
    server = cfg.get("server")
    if not isinstance(server, dict):
        server = {}
        cfg["server"] = server
    prev_host = filled_str(server.get("host"))
    tun = cfg.get("tun")
    if not isinstance(tun, dict):
        tun = {}
        cfg["tun"] = tun
    prev_sing_box = filled_str(tun.get("sing_box_path"))

    cfg["corporate"] = corporate
    cfg["http_bridge_port"] = _as_int(get("httpBridgePort"), 1088)
    if corporate:
        apply_corporate_profile(cfg)
        if bool(get("tunAuto") or get("killSwitch")):
            cfg["socks_scope"] = "full"
        else:
            cfg["socks_scope"] = filled_str(get("socksScope")) or "github"
        cfg["use_proxy"] = True
    else:
        apply_standard_profile(cfg)
        cfg["use_proxy"] = bool(get("useProxy"))
    assign_filled(cfg, "corporate_proxy", get("corporateProxy"))
    if not filled_str(cfg.get("corporate_proxy")) and prev_proxy:
        cfg["corporate_proxy"] = prev_proxy
    cfg.pop("ssh", None)
    assign_filled(server, "host", get("serverHost"))
    if not filled_str(server.get("host")) and prev_host:
        server["host"] = prev_host
    server["port"] = _as_int(get("serverPort"), 443)
    server["local_socks_port"] = _as_int(get("serverSocks"), 1080)
    cfg.pop("worker_base_url", None)
    ui_bypass = [
        x.strip() for x in filled_str(get("proxyBypass")).split(",") if x.strip()
    ]
    if ui_bypass:
        cfg["proxy_bypass"] = ui_bypass
    elif prev_bypass:
        cfg["proxy_bypass"] = prev_bypass
    if corporate:
        cfg["proxy_bypass_via"] = "direct"
    cfg["kill_switch"] = bool(get("killSwitch"))
    cfg["git_proxy"] = bool(get("gitProxy"))
    cfg["docker_proxy"] = bool(get("dockerProxy"))
    cfg["tun"] = tun
    tun["enabled"] = bool(get("tunAuto")) or cfg["kill_switch"]
    tun["elevate"] = True
    if prev_sing_box:
        tun["sing_box_path"] = prev_sing_box
    tr = cfg.get("transport")
    if not isinstance(tr, dict):
        tr = {}
        cfg["transport"] = tr
    tr["type"] = "vless-reality"
    tr["dial"] = normalize_dial(get("trDial"))
    assign_filled(tr, "uuid", get("trUuid"))
    assign_filled(tr, "public_key", get("trPublicKey"))
    assign_filled(tr, "short_id", get("trShortId"))
    assign_filled(tr, "server_name", get("trServerName"))
    if not filled_str(tr.get("server_name")):
        tr["server_name"] = REALITY_DEFAULT_SNI
    tr["port"] = _as_int(get("serverPort") or get("trPort"), 443)
    hy = tr.get("hysteria2")
    if not isinstance(hy, dict):
        hy = {}
        tr["hysteria2"] = hy
    assign_filled(hy, "password", get("hy2Password"))
    hy["port"] = _as_int(get("hy2Port"), 8443)
    hy_sni = filled_str(get("hy2ServerName"))
    if hy_sni:
        hy["server_name"] = normalize_hy2_sni(hy_sni)
    assign_filled(hy, "obfs_password", get("hy2Obfs"))
    hy["insecure"] = bool(hy.get("insecure", True))
    awg = tr.get("amneziawg")
    if not isinstance(awg, dict):
        awg = {}
        tr["amneziawg"] = awg
    assign_filled(awg, "private_key", get("awgPrivateKey"))
    assign_filled(awg, "peer_public_key", get("awgPeerPublicKey"))
    assign_filled(awg, "pre_shared_key", get("awgPresharedKey"))
    assign_filled(awg, "address", get("awgAddress") or AWG_DEFAULT_ADDRESS)
    awg["port"] = _as_int(get("awgPort"), AWG_DEFAULT_PORT)
    awg["mtu"] = _as_int(get("awgMtu"), AWG_DEFAULT_MTU)
    _set_awg_int(awg, "jc", get("awgJc"))
    _set_awg_int(awg, "jmin", get("awgJmin"))
    _set_awg_int(awg, "jmax", get("awgJmax"))
    _set_awg_int(awg, "s1", get("awgS1"))
    _set_awg_int(awg, "s2", get("awgS2"))
    assign_filled(awg, "h1", get("awgH1"))
    assign_filled(awg, "h2", get("awgH2"))
    assign_filled(awg, "h3", get("awgH3"))
    assign_filled(awg, "h4", get("awgH4"))
    rev = cfg.get("reverse_ssh")
    if not isinstance(rev, dict):
        rev = {}
        cfg["reverse_ssh"] = rev
    rev["enabled"] = bool(get("reverseSsh"))
    rev["listen_port"] = _as_int(get("reverseSshListen"), 2222)
    assign_filled(rev, "vps_user", get("reverseSshVpsUser"))
    if not filled_str(rev.get("vps_user")):
        rev["vps_user"] = "root"
    rev["vps_port"] = _as_int(get("reverseSshVpsPort"), 22)
    return cfg
