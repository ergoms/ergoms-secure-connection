"""Connection session, plan, and pipeline phase sequences."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from desktop.config_io import default_config_template, ensure_config_defaults
from desktop.lifecycle.actions import Action, action_from_cli, action_from_flag
from desktop.lifecycle.pipeline import ConnectionPipeline
from desktop.lifecycle.plan import resolve_connect_plan
from desktop.lifecycle.session import ConnectionSession, SessionBusy
from desktop.lifecycle.snapshot import Phase, Snapshot
from desktop.services.status import present_status
from desktop.ui.messages import format_user_error


def test_action_table_is_single_vocabulary() -> None:
    assert action_from_cli("on") is Action.CONNECT
    assert action_from_cli("start") is Action.CONNECT
    assert action_from_cli("off") is Action.DISCONNECT
    assert action_from_cli("tun-on") is Action.TUN_ON
    assert action_from_flag("--disconnect") is Action.DISCONNECT
    assert Action.TUN_ON.busy_intent == "on"
    assert Action.TUN_OFF.busy_intent == "off"
    assert Action.RECONNECT.elevation_key == "on"
    assert Action.CONNECT.client_method == "enable"
    assert Action.TUN_OFF.client_method == "disable_tun"
    assert Action.DISCONNECT.ru_name == "отключение"
    assert "disconnect" not in format_user_error("уже выполняется disconnect").lower()
    assert Action.CONNECT.ru_name == "подключение"


def test_session_logs_every_phase_change() -> None:
    logs: list[str] = []
    session = ConnectionSession(log=logs.append)
    session.transition(Phase.PREPARING, "старт")
    session.transition(Phase.LAUNCHING, "sing-box")
    session.transition(Phase.UP, "выход живой")
    assert [s.partition(" → ")[2].split(":")[0] for s in logs] == [
        "preparing",
        "launching",
        "up",
    ]
    assert session.snapshot.phase is Phase.UP


def test_session_operation_serializes_and_times_out() -> None:
    session = ConnectionSession(log=lambda _m: None, op_timeout=0.01)
    with session.operation(Action.CONNECT):
        with pytest.raises(SessionBusy, match="подключение"):
            with session.operation(Action.DISCONNECT):
                pass
    with session.operation(Action.DISCONNECT):
        assert session.current_action is Action.DISCONNECT
    assert session.in_operation is False


def test_present_status_accepts_snapshot() -> None:
    view = present_status(
        Snapshot(
            phase=Phase.UP,
            singbox_running=True,
            tun_running=True,
            tun_wanted=True,
            tun_ready=True,
            socks_up=True,
        ),
        config_ready=True,
    )
    assert view.title == "Защищено"
    assert view.signature
    connecting = present_status(
        Snapshot(phase=Phase.PROBING, singbox_running=True, tun_wanted=True),
        config_ready=True,
    )
    assert connecting.title == "Подключение…"


def _cfg(*, office: bool, dial: str, tun: bool, ks: bool) -> dict[str, Any]:
    cfg = ensure_config_defaults(default_config_template())
    cfg["server"]["host"] = "203.0.113.10"
    cfg["corporate"] = office
    cfg["use_proxy"] = office
    cfg["corporate_proxy"] = "192.0.2.10:3128" if office else ""
    cfg["tun"]["enabled"] = tun
    cfg["kill_switch"] = ks
    cfg["transport"]["dial"] = dial
    cfg["transport"]["uuid"] = "11111111-1111-1111-1111-111111111111"
    cfg["transport"]["public_key"] = "pub-key"
    if dial == "amneziawg":
        cfg["transport"]["amneziawg"]["private_key"] = "priv"
        cfg["transport"]["amneziawg"]["peer_public_key"] = "pub"
    return cfg


@pytest.mark.parametrize(
    ("office", "dial", "tun", "ks", "start_tun", "start_ks", "tun_deferred", "ks_deferred"),
    [
        (True, "vless-reality", True, True, True, True, False, False),
        (True, "vless-reality", False, False, False, False, False, False),
        (False, "amneziawg", True, True, True, False, False, True),
        (False, "amneziawg", False, False, False, False, False, False),
        (True, "amneziawg", True, True, True, False, False, True),
        (False, "vless-reality", True, True, True, True, False, False),
    ],
)
def test_resolve_connect_plan_six_combos(
    office: bool,
    dial: str,
    tun: bool,
    ks: bool,
    start_tun: bool,
    start_ks: bool,
    tun_deferred: bool,
    ks_deferred: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "desktop.lifecycle.plan.kill_switch_allow_ips",
        lambda *hosts: [str(h) for h in hosts],
    )
    plan = resolve_connect_plan(
        _cfg(office=office, dial=dial, tun=tun, ks=ks),
        platform="win32",
        tun_enabled=tun,
        kill_switch=ks,
    )
    assert plan.dial == dial
    assert plan.start_tun is start_tun
    assert plan.start_ks is start_ks
    assert plan.tun_deferred is tun_deferred
    assert plan.ks_deferred is ks_deferred
    assert plan.tun_wanted is (tun or ks)


class _FakeSingbox:
    def __init__(self) -> None:
        self.alive = False
        self.starts: list[dict[str, Any]] = []

    def running(self) -> bool:
        return self.alive

    def pid(self) -> int | None:
        return 42 if self.alive else None

    def start(self, **kwargs: Any) -> None:
        self.starts.append(kwargs)
        self.alive = True

    def stop(self) -> None:
        self.alive = False

    def find_sing_box(self, _path: str = "") -> object:
        return True

    def find_awg_sing_box(self) -> object:
        return True

    def tail_log(self, _n: int = 20) -> list[str]:
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.session = ConnectionSession(log=self.logs.append)
        self.singbox = _FakeSingbox()
        self.tun = MagicMock()
        self.tun.running.return_value = False
        self.paths = MagicMock()
        self.paths.state_path.is_file.return_value = False
        self.paths.var_dir = MagicMock()
        self._inprocess_helpers = True
        self._atexit_done = False
        self._awg_tun_kwargs = None
        self._pac_server = None
        self.pipeline = ConnectionPipeline(self)
        self._probed = False

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def reload_env(self) -> None:
        return None

    def config(self) -> dict[str, Any]:
        return _cfg(office=False, dial="vless-reality", tun=False, ks=False)

    def probe(self, host: str, port: int = 443) -> int:
        del host, port
        return 0

    def reap_leftovers(self, *, socks_port: int | None = None) -> None:
        del socks_port

    def set_git_singbox(self, cfg: dict[str, Any], http_port: int) -> None:
        del cfg, http_port

    def _maybe_start_reverse_ssh(self, cfg: dict[str, Any] | None = None) -> None:
        del cfg

    def _log_foreign_vpn(self, host: str, office_proxy: str) -> list[str]:
        del host, office_proxy
        return []

    def _ensure_kill_switch(self, cfg: dict[str, Any]) -> list[str]:
        del cfg
        return []

    def _log_singbox_tail(self, reason: str) -> None:
        del reason

    def _pin_underlay_later(self, allow: list[str]) -> None:
        del allow

    def _probe_exit(self, socks_port: int, delay: float = 0.0) -> None:
        del socks_port, delay
        self._probed = True

    def _stop_session_core(self, *, scan_helpers: bool = False) -> Exception | None:
        del scan_helpers
        self.singbox.stop()
        return None

    def stop_watchdog_daemon(self) -> None:
        return None

    def ensure_watchdog_daemon(self) -> None:
        return None

    def teardown_overrides(self) -> None:
        return None

    def teardown_overrides_if_dirty(self) -> None:
        return None

    def _reap_helpers(self, *, scan_cmdline: bool = False) -> None:
        del scan_cmdline


def test_pipeline_connect_reaches_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop.lifecycle.plan import ConnectPlan

    plan = ConnectPlan(
        host="203.0.113.10",
        port=443,
        dial="vless-reality",
        awg=None,
        office_proxy="",
        transport={"type": "vless-reality", "uuid": "u", "public_key": "k"},
        socks_port=1080,
        http_port=1088,
        sing_box_path="",
        bypass=["*.local"],
        vpn_hosts=[],
        mtu=1400,
        vps_proxy_ports=[22],
        elevate=False,
        scope="full",
        tun_wanted=False,
        tun_deferred=False,
        start_tun=False,
        ks_wanted=False,
        ks_deferred=False,
        start_ks=False,
        allow=["203.0.113.10"],
    )
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.resolve_connect_plan", lambda _cfg: plan
    )
    monkeypatch.setattr("desktop.lifecycle.pipeline.port_open", lambda *_a, **_k: False)
    monkeypatch.setattr("desktop.lifecycle.pipeline.procutil.is_admin", lambda: False)
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.remember_kill_switch_plan", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.socket.create_connection",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("skip")),
    )
    client = _FakeClient()
    client.pipeline.connect(spawn_watchdog=False)
    phases = [ln for ln in client.logs if ln.startswith("фаза ")]
    assert any("preparing" in ln for ln in phases)
    assert any("launching" in ln for ln in phases)
    assert any("probing" in ln for ln in phases)
    assert client.session.snapshot.phase is Phase.UP
    assert client._probed is True
    assert client.singbox.alive is True


def test_pipeline_awg_starts_tun_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop.lifecycle.plan import ConnectPlan

    plan = ConnectPlan(
        host="203.0.113.10",
        port=51821,
        dial="amneziawg",
        awg={"port": 51821, "private_key": "p", "peer_public_key": "k"},
        office_proxy="",
        transport={"type": "amneziawg", "amneziawg": {}},
        socks_port=1080,
        http_port=1088,
        sing_box_path="",
        bypass=["*.local"],
        vpn_hosts=[],
        mtu=1400,
        vps_proxy_ports=[22],
        elevate=False,
        scope="full",
        tun_wanted=True,
        tun_deferred=False,
        start_tun=True,
        ks_wanted=True,
        ks_deferred=True,
        start_ks=False,
        allow=["203.0.113.10"],
    )
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.resolve_connect_plan", lambda _cfg: plan
    )
    monkeypatch.setattr("desktop.lifecycle.pipeline.port_open", lambda *_a, **_k: False)
    monkeypatch.setattr("desktop.lifecycle.pipeline.procutil.is_admin", lambda: False)
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.kill_switch_is_applied", lambda force=False: False
    )
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.kill_switch_pin_cmds", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.remember_kill_switch_plan", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "desktop.lifecycle.pipeline.remove_stale_tun_adapter", lambda **_k: True
    )
    monkeypatch.setattr("desktop.lifecycle.pipeline.sys.platform", "win32")
    client = _FakeClient()
    client.tun.awg_version_ok.return_value = True
    client.pipeline.connect(spawn_watchdog=False)
    assert len(client.singbox.starts) == 1
    assert client.singbox.starts[0]["enable_tun"] is True
    assert any("TUN inbound сразу" in ln for ln in client.logs)
    assert not any("handshake без TUN" in ln for ln in client.logs)
    assert client.session.snapshot.pending_awg_tun is False
    assert client.session.snapshot.phase is Phase.UP


def test_pipeline_disconnect_returns_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("desktop.lifecycle.pipeline.kill_switch_is_applied", lambda force=False: False)
    monkeypatch.setattr("desktop.lifecycle.pipeline.our_tun_split_leftover", lambda: False)
    monkeypatch.setattr("desktop.lifecycle.pipeline.procutil.invalidate_proc_cache", lambda: None)
    client = _FakeClient()
    client.singbox.alive = True
    client.session.transition(Phase.UP, "тест")
    client.pipeline.disconnect()
    assert client.session.snapshot.phase is Phase.IDLE
    assert client.singbox.alive is False


def test_pipeline_fail_closed_phase() -> None:
    client = _FakeClient()
    client.session.update(fail_closed=True, exit_probe_error="timeout")
    client.pipeline._finish_after_probe()
    assert client.session.snapshot.phase is Phase.FAIL_CLOSED


def test_pipeline_degraded_phase() -> None:
    client = _FakeClient()
    client.session.update(exit_probe_error="https fail")
    client.pipeline._finish_after_probe()
    assert client.session.snapshot.phase is Phase.DEGRADED


def test_watchdog_uses_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop.watchdog import TunnelWatchdog

    calls: list[str] = []

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
            calls.append("enable")

        def enable_tun(self, *, persist: bool = True) -> None:
            del persist
            calls.append("tun")

        def reconnect(self, *, spawn_watchdog: bool = True) -> None:
            del spawn_watchdog
            calls.append("reconnect")

        def stop_singbox_mode(self, *, teardown: bool = True) -> None:
            calls.append(f"stop:{teardown}")

        def config(self) -> dict:
            return {}

        def _ensure_kill_switch(self, cfg: dict) -> list[str]:
            del cfg
            return []

        def _maybe_start_reverse_ssh(self, cfg: dict | None = None) -> None:
            del cfg

    monkeypatch.setattr("desktop.watchdog.health_problem", lambda *_a, **_k: None)
    wd = TunnelWatchdog(Host())  # type: ignore[arg-type]
    wd._reconnect(tun_only=False)  # noqa: SLF001
    assert calls == ["reconnect"]
    calls.clear()
    wd._reconnect(tun_only=True)  # noqa: SLF001
    assert calls == ["tun"]
