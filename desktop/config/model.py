"""Typed config model. Field defaults are the single source of truth."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from desktop.config.constants import (
    AWG_DEFAULT_ADDRESS,
    AWG_DEFAULT_MTU,
    AWG_DEFAULT_PORT,
    BLOCKED_HOSTS,
    DEFAULT_DIAL,
    DEFAULT_SERVER_HOST,
    HTTP_BRIDGE_PORT,
    LOCAL_SOCKS_PORT,
    PAC_LISTEN_PORT,
    REALITY_DEFAULT_SNI,
    REVERSE_SSH_LISTEN,
    REVERSE_SSH_VPS_PORT,
    SERVER_PORT,
    STANDARD_BYPASS_PRESET,
    TUN_DEFAULT_MTU,
    TUN_MTU_MAX,
    TUN_MTU_MIN,
    WATCHDOG_INTERVAL,
    WATCHDOG_MAX_RETRIES,
)

_ENV_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})


def truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return str(val or "").strip().lower() in _ENV_BOOL_TRUE


def as_bool(val: Any, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    return truthy(val)


def as_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def clamp_port(val: Any, default: int) -> int:
    return max(1, min(65535, as_int(val, default)))


def clamp_mtu(val: Any, default: int) -> int:
    return max(TUN_MTU_MIN, min(TUN_MTU_MAX, as_int(val, default)))


def normalize_dial(value: Any) -> str:
    """Reality or AmneziaWG. Empty / legacy `auto` / Hy2 → AmneziaWG."""
    raw = str(value or "").strip().lower()
    if raw in ("vless", "reality", "vless-reality"):
        return "vless-reality"
    return DEFAULT_DIAL


def normalize_scope(value: Any) -> str:
    scope = str(value or "full").strip().lower()
    if scope in ("full", "all", "system"):
        return "full"
    if scope in ("github", "pac", "partial"):
        return "github"
    return "full"


def filled_str(value: Any) -> str:
    return str(value or "").strip()


def assign_filled(dst: dict[str, Any], key: str, incoming: Any) -> None:
    text = filled_str(incoming)
    if text:
        dst[key] = text


def _section(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


@dataclass
class ServerConfig:
    host: str = DEFAULT_SERVER_HOST
    port: int = SERVER_PORT
    local_socks_port: int = LOCAL_SOCKS_PORT

    def __post_init__(self) -> None:
        self.host = str(self.host or "").strip()
        self.port = clamp_port(self.port, SERVER_PORT)
        self.local_socks_port = clamp_port(self.local_socks_port, LOCAL_SOCKS_PORT)

    @classmethod
    def from_dict(cls, raw: Any, *, legacy_ssh: dict[str, Any] | None = None) -> ServerConfig:
        data = _section(raw)
        if legacy_ssh:
            for key in ("host", "port", "local_socks_port"):
                if key in legacy_ssh and key not in data:
                    data[key] = legacy_ssh[key]
        return cls(
            host=str(data["host"]) if "host" in data else DEFAULT_SERVER_HOST,
            port=as_int(data.get("port"), SERVER_PORT),
            local_socks_port=as_int(data.get("local_socks_port"), LOCAL_SOCKS_PORT),
        )


@dataclass
class TunConfig:
    enabled: bool = True
    elevate: bool = True
    sing_box_path: str = ""
    mtu: int = TUN_DEFAULT_MTU

    def __post_init__(self) -> None:
        self.enabled = as_bool(self.enabled, True)
        self.elevate = as_bool(self.elevate, True)
        self.sing_box_path = str(self.sing_box_path or "").strip()
        self.mtu = clamp_mtu(self.mtu, TUN_DEFAULT_MTU)

    @classmethod
    def from_dict(cls, raw: Any, *, tun_enabled: Any = None) -> TunConfig:
        data = _section(raw)
        if "enabled" not in data and tun_enabled is not None:
            data["enabled"] = tun_enabled
        return cls(
            enabled=as_bool(data.get("enabled"), True),
            elevate=as_bool(data.get("elevate"), True),
            sing_box_path=str(data.get("sing_box_path") or ""),
            mtu=as_int(data.get("mtu"), TUN_DEFAULT_MTU),
        )


@dataclass
class AmneziaWgConfig:
    port: int = AWG_DEFAULT_PORT
    private_key: str = ""
    peer_public_key: str = ""
    pre_shared_key: str = ""
    address: str = AWG_DEFAULT_ADDRESS
    mtu: int = AWG_DEFAULT_MTU
    jc: int = 0
    jmin: int = 0
    jmax: int = 0
    s1: int = 0
    s2: int = 0
    h1: str = ""
    h2: str = ""
    h3: str = ""
    h4: str = ""
    keepalive: int = 25

    def __post_init__(self) -> None:
        self.port = clamp_port(self.port, AWG_DEFAULT_PORT)
        self.private_key = str(self.private_key or "").strip()
        self.peer_public_key = str(self.peer_public_key or "").strip()
        self.pre_shared_key = str(self.pre_shared_key or "").strip()
        self.address = str(self.address or AWG_DEFAULT_ADDRESS).strip() or AWG_DEFAULT_ADDRESS
        self.mtu = clamp_mtu(self.mtu, AWG_DEFAULT_MTU)
        self.jc = max(0, as_int(self.jc, 0))
        self.jmin = max(0, as_int(self.jmin, 0))
        self.jmax = max(0, as_int(self.jmax, 0))
        self.s1 = max(0, as_int(self.s1, 0))
        self.s2 = max(0, as_int(self.s2, 0))
        self.h1 = str(self.h1 or "").strip()
        self.h2 = str(self.h2 or "").strip()
        self.h3 = str(self.h3 or "").strip()
        self.h4 = str(self.h4 or "").strip()
        self.keepalive = max(0, min(600, as_int(self.keepalive, 25)))

    @classmethod
    def from_dict(cls, raw: Any) -> AmneziaWgConfig:
        data = _section(raw)
        known = {f.name for f in fields(cls)}
        kwargs = {name: data[name] for name in known if name in data}
        return cls(**kwargs)


@dataclass
class TransportConfig:
    type: str = "vless-reality"
    dial: str = DEFAULT_DIAL
    uuid: str = ""
    public_key: str = ""
    short_id: str = ""
    server_name: str = REALITY_DEFAULT_SNI
    port: int = SERVER_PORT
    amneziawg: AmneziaWgConfig = field(default_factory=AmneziaWgConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.amneziawg, AmneziaWgConfig):
            self.amneziawg = AmneziaWgConfig.from_dict(self.amneziawg)
        self.type = "vless-reality"
        self.dial = normalize_dial(self.dial)
        self.uuid = str(self.uuid or "").strip()
        self.public_key = str(self.public_key or "").strip()
        self.short_id = str(self.short_id or "").strip()
        self.server_name = str(self.server_name or REALITY_DEFAULT_SNI).strip() or REALITY_DEFAULT_SNI
        self.port = clamp_port(self.port, SERVER_PORT)

    @classmethod
    def from_dict(cls, raw: Any) -> TransportConfig:
        data = _section(raw)
        return cls(
            type=str(data.get("type") or "vless-reality"),
            dial=normalize_dial(data.get("dial")),
            uuid=str(data.get("uuid") or ""),
            public_key=str(data.get("public_key") or ""),
            short_id=str(data.get("short_id") or ""),
            server_name=str(data.get("server_name") or REALITY_DEFAULT_SNI),
            port=as_int(data.get("port"), SERVER_PORT),
            amneziawg=AmneziaWgConfig.from_dict(data.get("amneziawg")),
        )


@dataclass
class ReverseSshConfig:
    enabled: bool = False
    vps_user: str = "root"
    vps_port: int = REVERSE_SSH_VPS_PORT
    listen_port: int = REVERSE_SSH_LISTEN
    local_port: int = 22
    identity_file: str = ""

    def __post_init__(self) -> None:
        self.enabled = as_bool(self.enabled, False)
        self.vps_user = str(self.vps_user or "root").strip() or "root"
        self.vps_port = clamp_port(self.vps_port, REVERSE_SSH_VPS_PORT)
        self.listen_port = clamp_port(self.listen_port, REVERSE_SSH_LISTEN)
        self.local_port = clamp_port(self.local_port, 22)
        self.identity_file = str(self.identity_file or "").strip()

    @classmethod
    def from_dict(cls, raw: Any) -> ReverseSshConfig:
        data = _section(raw)
        return cls(
            enabled=as_bool(data.get("enabled"), False),
            vps_user=str(data.get("vps_user") or "root"),
            vps_port=as_int(data.get("vps_port"), REVERSE_SSH_VPS_PORT),
            listen_port=as_int(data.get("listen_port"), REVERSE_SSH_LISTEN),
            local_port=as_int(data.get("local_port"), 22),
            identity_file=str(data.get("identity_file") or ""),
        )


@dataclass
class AppConfig:
    corporate: bool = False
    use_proxy: bool = False
    corporate_proxy: str = ""
    socks_scope: str = "full"
    http_bridge_port: int = HTTP_BRIDGE_PORT
    pac_listen_port: int = PAC_LISTEN_PORT
    watchdog: bool = True
    watchdog_interval: int = WATCHDOG_INTERVAL
    watchdog_max_retries: int = WATCHDOG_MAX_RETRIES
    kill_switch: bool = True
    git_proxy: bool = False
    docker_proxy: bool = False
    server: ServerConfig = field(default_factory=ServerConfig)
    blocked_hosts: list[str] = field(default_factory=lambda: list(BLOCKED_HOSTS))
    proxy_bypass: list[str] = field(default_factory=lambda: list(STANDARD_BYPASS_PRESET))
    proxy_bypass_via: str = "direct"
    tun: TunConfig = field(default_factory=TunConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    reverse_ssh: ReverseSshConfig = field(default_factory=ReverseSshConfig)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.server, ServerConfig):
            self.server = ServerConfig.from_dict(self.server)
        if not isinstance(self.tun, TunConfig):
            self.tun = TunConfig.from_dict(self.tun)
        if not isinstance(self.transport, TransportConfig):
            self.transport = TransportConfig.from_dict(self.transport)
        if not isinstance(self.reverse_ssh, ReverseSshConfig):
            self.reverse_ssh = ReverseSshConfig.from_dict(self.reverse_ssh)
        self.corporate = as_bool(self.corporate, False)
        self.use_proxy = as_bool(self.use_proxy, False) or self.corporate
        self.corporate_proxy = str(self.corporate_proxy or "").strip()
        self.socks_scope = normalize_scope(self.socks_scope)
        self.http_bridge_port = as_int(self.http_bridge_port, HTTP_BRIDGE_PORT)
        self.pac_listen_port = as_int(self.pac_listen_port, PAC_LISTEN_PORT)
        self.watchdog = as_bool(self.watchdog, True)
        self.watchdog_interval = max(5, as_int(self.watchdog_interval, WATCHDOG_INTERVAL))
        self.watchdog_max_retries = max(0, as_int(self.watchdog_max_retries, WATCHDOG_MAX_RETRIES))
        self.kill_switch = as_bool(self.kill_switch, True)
        self.git_proxy = as_bool(self.git_proxy, False)
        self.docker_proxy = as_bool(self.docker_proxy, False)
        self.blocked_hosts = [str(x) for x in (self.blocked_hosts or [])]
        self.proxy_bypass = [str(x) for x in (self.proxy_bypass or list(STANDARD_BYPASS_PRESET))]
        self.proxy_bypass_via = str(self.proxy_bypass_via or "direct").strip() or "direct"
        if not isinstance(self.extra, dict):
            self.extra = {}

    @classmethod
    def from_dict(cls, raw: Any) -> AppConfig:
        data = _section(raw)
        known = {
            f.name
            for f in fields(cls)
            if f.name != "extra"
        }
        extra = {k: v for k, v in data.items() if k not in known and k not in ("ssh", "tun_enabled", "worker_base_url")}
        corporate = as_bool(data.get("corporate"), False)
        git_present = "git_proxy" in data
        docker_present = "docker_proxy" in data
        return cls(
            corporate=corporate,
            use_proxy=as_bool(data.get("use_proxy"), False),
            corporate_proxy=str(data.get("corporate_proxy") or ""),
            socks_scope=str(data.get("socks_scope") or "full"),
            http_bridge_port=as_int(data.get("http_bridge_port"), HTTP_BRIDGE_PORT),
            pac_listen_port=as_int(data.get("pac_listen_port"), PAC_LISTEN_PORT),
            watchdog=as_bool(data.get("watchdog"), True),
            watchdog_interval=as_int(data.get("watchdog_interval"), WATCHDOG_INTERVAL),
            watchdog_max_retries=as_int(data.get("watchdog_max_retries"), WATCHDOG_MAX_RETRIES),
            kill_switch=as_bool(data.get("kill_switch"), True),
            git_proxy=as_bool(data.get("git_proxy"), corporate) if git_present else corporate,
            docker_proxy=as_bool(data.get("docker_proxy"), corporate) if docker_present else corporate,
            server=ServerConfig.from_dict(data.get("server"), legacy_ssh=_section(data.get("ssh"))),
            blocked_hosts=list(data.get("blocked_hosts") or BLOCKED_HOSTS),
            proxy_bypass=list(data.get("proxy_bypass") or STANDARD_BYPASS_PRESET),
            proxy_bypass_via=str(data.get("proxy_bypass_via") or "direct"),
            tun=TunConfig.from_dict(data.get("tun"), tun_enabled=data.get("tun_enabled")),
            transport=TransportConfig.from_dict(data.get("transport")),
            reverse_ssh=ReverseSshConfig.from_dict(data.get("reverse_ssh")),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        extra = payload.pop("extra", {}) or {}
        out = dict(payload)
        for key, val in extra.items():
            if key not in out:
                out[key] = val
        return out


def default_amneziawg_block() -> dict[str, Any]:
    return asdict(AmneziaWgConfig())


def default_config_template() -> dict[str, Any]:
    return AppConfig().to_dict()


def ensure_config_defaults(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return AppConfig.from_dict(cfg or {}).to_dict()
