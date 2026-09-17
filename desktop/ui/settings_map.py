"""QML settings keys ↔ config.json. One mapping for defaults / load / save."""

from __future__ import annotations

from typing import Any, Callable

from desktop.config.model import AppConfig
from desktop.config_io import (
    AWG_CONF_NAME,
    AWG_DEFAULT_ADDRESS,
    AWG_DEFAULT_MTU,
    AWG_DEFAULT_PORT,
    CORPORATE_BYPASS_PRESET,
    CORPORATE_PROXY_PRESET,
    REALITY_DEFAULT_SNI,
    STANDARD_BYPASS_PRESET,
    apply_corporate_profile,
    apply_standard_profile,
    assign_filled,
    filled_str,
    infer_corporate,
    normalize_dial,
)
from desktop.route_tokens import tokens_from_json, tokens_json

GetFn = Callable[[str], Any]


def settings_defaults() -> dict[str, Any]:
    """UI defaults derived from AppConfig so ports/dials cannot drift."""
    app = AppConfig()
    return {
        "corporate": app.corporate,
        "useProxy": app.use_proxy,
        "socksScope": app.socks_scope,
        "tunAuto": app.tun.enabled,
        "killSwitch": app.kill_switch,
        "rustdesk": app.rustdesk,
        "gitProxy": app.git_proxy,
        "gitVia": app.git_via,
        "dockerProxy": app.docker_proxy,
        "httpBridgePort": str(app.http_bridge_port),
        "corporateProxy": app.corporate_proxy,
        "serverHost": "",
        "serverPort": str(app.server.port),
        "serverSocks": str(app.server.local_socks_port),
        "proxyBypass": "",
        "proxyBypassVia": app.proxy_bypass_via,
        "routeDirect": "[]",
        "routeVpn": "[]",
        "trUuid": app.transport.uuid,
        "trPublicKey": app.transport.public_key,
        "trShortId": app.transport.short_id,
        "trServerName": app.transport.server_name,
        "trPort": str(app.transport.port),
        "trDial": app.transport.dial,
        "awgPrivateKey": app.transport.amneziawg.private_key,
        "awgPeerPublicKey": app.transport.amneziawg.peer_public_key,
        "awgPresharedKey": app.transport.amneziawg.pre_shared_key,
        "awgAddress": app.transport.amneziawg.address,
        "awgPort": str(app.transport.amneziawg.port),
        "awgMtu": str(app.transport.amneziawg.mtu),
        "awgJc": str(app.transport.amneziawg.jc),
        "awgJmin": str(app.transport.amneziawg.jmin),
        "awgJmax": str(app.transport.amneziawg.jmax),
        "awgS1": str(app.transport.amneziawg.s1),
        "awgS2": str(app.transport.amneziawg.s2),
        "awgH1": app.transport.amneziawg.h1,
        "awgH2": app.transport.amneziawg.h2,
        "awgH3": app.transport.amneziawg.h3,
        "awgH4": app.transport.amneziawg.h4,
        "awgLoaded": False,
        "awgSummary": "",
        "reverseSsh": app.reverse_ssh.enabled,
        "reverseSshListen": str(app.reverse_ssh.listen_port),
        "reverseSshVpsUser": app.reverse_ssh.vps_user,
        "reverseSshVpsPort": str(app.reverse_ssh.vps_port),
    }


def cfg_to_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    server = cfg.get("server") or {}
    tun = cfg.get("tun") or {}
    tr = cfg.get("transport") or {}
    bypass = [str(x).strip() for x in (cfg.get("proxy_bypass") or []) if str(x).strip()]
    blocked = [str(x).strip() for x in (cfg.get("blocked_hosts") or []) if str(x).strip()]
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
            "rustdesk": bool(cfg.get("rustdesk", True)),
            "gitProxy": True,
            "gitVia": "tun",
            "dockerProxy": False,
            "httpBridgePort": str(cfg.get("http_bridge_port") or 1088),
            "useProxy": bool(cfg.get("use_proxy")) or corporate,
            "corporateProxy": str(cfg.get("corporate_proxy") or ""),
            "serverHost": str(server.get("host") or ""),
            "serverPort": str(server.get("port") or 443),
            "serverSocks": str(server.get("local_socks_port") or 1080),
            "proxyBypass": ", ".join(bypass),
            "proxyBypassVia": str(cfg.get("proxy_bypass_via") or "direct"),
            "routeDirect": tokens_json(bypass),
            "routeVpn": tokens_json(blocked),
            "trUuid": str(tr.get("uuid") or ""),
            "trPublicKey": str(tr.get("public_key") or ""),
            "trShortId": str(tr.get("short_id") or ""),
            "trServerName": str(tr.get("server_name") or REALITY_DEFAULT_SNI),
            "trPort": str(tr.get("port") or 443),
            "trDial": normalize_dial(tr.get("dial")),
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
            "awgLoaded": bool(
                str(awg.get("private_key") or "").strip()
                and str(awg.get("peer_public_key") or "").strip()
            ),
            "awgSummary": "",
            "reverseSsh": bool(rev.get("enabled", False)),
            "reverseSshListen": str(rev.get("listen_port") or 2222),
            "reverseSshVpsUser": str(rev.get("vps_user") or "root"),
            "reverseSshVpsPort": str(rev.get("vps_port") or 22),
        }
    )
    host = str(server.get("host") or "").strip()
    if out["awgLoaded"]:
        out["awgSummary"] = AWG_CONF_NAME
    return out


def apply_mode_to_settings(
    get: GetFn,
    put: Callable[[str, Any], None],
    *,
    corporate: bool,
) -> None:
    """Flip office/home. User-entered proxy, bypass and dial stay."""
    put("corporate", corporate)
    put("useProxy", bool(corporate))
    if corporate:
        tun = bool(get("tunAuto") or get("killSwitch"))
        put("socksScope", "full" if tun else "github")
        if not filled_str(get("corporateProxy")) and CORPORATE_PROXY_PRESET:
            put("corporateProxy", CORPORATE_PROXY_PRESET)
        if not _bypass_filled(get):
            _put_direct(put, CORPORATE_BYPASS_PRESET)
        if not filled_str(get("trDial")):
            put("trDial", "vless-reality")
        return
    put("socksScope", "full")
    if not _bypass_filled(get):
        _put_direct(put, STANDARD_BYPASS_PRESET)
    if not filled_str(get("trDial")):
        put("trDial", "amneziawg")


def _put_direct(put: Callable[[str, Any], None], items: list[str]) -> None:
    put("proxyBypass", ", ".join(items))
    put("routeDirect", tokens_json(items))


def _bypass_filled(get: GetFn) -> bool:
    if filled_str(get("proxyBypass")):
        return True
    return bool(tokens_from_json(get("routeDirect")))


def _tokens_from_ui(get: GetFn, json_key: str, csv_key: str) -> list[str]:
    parsed = tokens_from_json(get(json_key))
    if parsed:
        return parsed
    if not csv_key:
        return []
    return [x.strip() for x in filled_str(get(csv_key)).split(",") if x.strip()]


def _as_int(raw: Any, default: int) -> int:
    try:
        return int(str(raw or "").strip() or str(default))
    except ValueError:
        return default


def apply_settings_to_cfg(
    cfg: dict[str, Any],
    get: GetFn,
    *,
    corporate: bool,
) -> dict[str, Any]:
    prev_bypass = [str(x).strip() for x in (cfg.get("proxy_bypass") or []) if str(x).strip()]
    prev_blocked = [str(x).strip() for x in (cfg.get("blocked_hosts") or []) if str(x).strip()]
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
    ui_bypass = _tokens_from_ui(get, "routeDirect", "proxyBypass")
    if ui_bypass:
        cfg["proxy_bypass"] = ui_bypass
    elif prev_bypass:
        cfg["proxy_bypass"] = prev_bypass
    ui_vpn = _tokens_from_ui(get, "routeVpn", "")
    if ui_vpn:
        cfg["blocked_hosts"] = ui_vpn
    elif prev_blocked:
        cfg["blocked_hosts"] = prev_blocked
    if corporate:
        cfg["proxy_bypass_via"] = "direct"
    cfg["kill_switch"] = bool(get("killSwitch"))
    cfg["rustdesk"] = bool(get("rustdesk"))
    cfg["git_proxy"] = True
    cfg["git_via"] = "tun"
    cfg["docker_proxy"] = False
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
    tr.pop("hysteria2", None)
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
