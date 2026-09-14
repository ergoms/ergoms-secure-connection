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
        "trDial": "hysteria2",
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


def apply_settings_to_cfg(
    cfg: dict[str, Any],
    get: GetFn,
    *,
    corporate: bool,
) -> dict[str, Any]:
    cfg["corporate"] = corporate
    cfg["http_bridge_port"] = int(
        str(get("httpBridgePort") or "1088").strip() or "1088"
    )
    if corporate:
        apply_corporate_profile(cfg)
        cfg["socks_scope"] = str(get("socksScope") or "github").strip() or "github"
        cfg["use_proxy"] = True
        cfg["corporate_proxy"] = str(get("corporateProxy") or "").strip()
    else:
        apply_standard_profile(cfg)
        cfg["use_proxy"] = bool(get("useProxy"))
        cfg["corporate_proxy"] = (
            str(get("corporateProxy") or "").strip() if cfg["use_proxy"] else ""
        )
    cfg.pop("ssh", None)
    cfg["server"] = {
        "host": str(get("serverHost") or "").strip(),
        "port": int(str(get("serverPort") or "443").strip() or "443"),
        "local_socks_port": int(str(get("serverSocks") or "1080").strip() or "1080"),
    }
    cfg.pop("worker_base_url", None)
    if corporate:
        raw_bypass = str(get("proxyBypass") or "").strip()
        cfg["proxy_bypass"] = [x.strip() for x in raw_bypass.split(",") if x.strip()]
        cfg["proxy_bypass_via"] = "direct"
    cfg["kill_switch"] = bool(get("killSwitch"))
    cfg["git_proxy"] = bool(get("gitProxy"))
    cfg["docker_proxy"] = bool(get("dockerProxy"))
    cfg.setdefault("tun", {})
    cfg["tun"]["enabled"] = bool(get("tunAuto")) or cfg["kill_switch"]
    cfg["tun"]["elevate"] = True
    cfg["tun"]["sing_box_path"] = ""
    cfg.setdefault("transport", {})
    cfg["transport"]["type"] = "vless-reality"
    if corporate:
        cfg["transport"]["dial"] = "vless-reality"
    else:
        cfg["transport"]["dial"] = normalize_dial(get("trDial"))
    cfg["transport"]["uuid"] = str(get("trUuid") or "").strip()
    cfg["transport"]["public_key"] = str(get("trPublicKey") or "").strip()
    cfg["transport"]["short_id"] = str(get("trShortId") or "").strip()
    cfg["transport"]["server_name"] = (
        str(get("trServerName") or "").strip() or REALITY_DEFAULT_SNI
    )
    cfg["transport"]["port"] = int(
        str(get("serverPort") or get("trPort") or "443").strip() or "443"
    )
    hy = cfg["transport"].setdefault("hysteria2", {})
    if not isinstance(hy, dict):
        hy = {}
        cfg["transport"]["hysteria2"] = hy
    hy_pw = str(get("hy2Password") or "").strip()
    if hy_pw:
        hy["password"] = hy_pw
    hy["port"] = int(str(get("hy2Port") or "8443").strip() or "8443")
    hy["server_name"] = normalize_hy2_sni(get("hy2ServerName"))
    hy_obfs = str(get("hy2Obfs") or "").strip()
    if hy_obfs:
        hy["obfs_password"] = hy_obfs
    elif "obfs_password" not in hy:
        hy["obfs_password"] = str(hy.get("obfs_password") or "")
    hy["insecure"] = bool(hy.get("insecure", True))
    awg = cfg["transport"].setdefault("amneziawg", {})
    if not isinstance(awg, dict):
        awg = {}
        cfg["transport"]["amneziawg"] = awg
    awg["private_key"] = str(get("awgPrivateKey") or "").strip()
    awg["peer_public_key"] = str(get("awgPeerPublicKey") or "").strip()
    awg["pre_shared_key"] = str(get("awgPresharedKey") or "").strip()
    awg["address"] = str(get("awgAddress") or "").strip() or AWG_DEFAULT_ADDRESS
    awg["port"] = int(
        str(get("awgPort") or str(AWG_DEFAULT_PORT)).strip() or str(AWG_DEFAULT_PORT)
    )
    awg["mtu"] = int(
        str(get("awgMtu") or str(AWG_DEFAULT_MTU)).strip() or str(AWG_DEFAULT_MTU)
    )
    awg["jc"] = int(str(get("awgJc") or "0").strip() or "0")
    awg["jmin"] = int(str(get("awgJmin") or "0").strip() or "0")
    awg["jmax"] = int(str(get("awgJmax") or "0").strip() or "0")
    awg["s1"] = int(str(get("awgS1") or "0").strip() or "0")
    awg["s2"] = int(str(get("awgS2") or "0").strip() or "0")
    awg["h1"] = str(get("awgH1") or "").strip()
    awg["h2"] = str(get("awgH2") or "").strip()
    awg["h3"] = str(get("awgH3") or "").strip()
    awg["h4"] = str(get("awgH4") or "").strip()
    cfg.setdefault("reverse_ssh", {})
    cfg["reverse_ssh"]["enabled"] = bool(get("reverseSsh"))
    cfg["reverse_ssh"]["listen_port"] = int(
        str(get("reverseSshListen") or "2222").strip() or "2222"
    )
    cfg["reverse_ssh"]["vps_user"] = (
        str(get("reverseSshVpsUser") or "").strip() or "root"
    )
    cfg["reverse_ssh"]["vps_port"] = int(
        str(get("reverseSshVpsPort") or "22").strip() or "22"
    )
    return cfg
