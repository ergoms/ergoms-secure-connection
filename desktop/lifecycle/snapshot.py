"""Immutable connection snapshot and explicit phases."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    LAUNCHING = "launching"
    WAITING_SOCKS = "waiting_socks"
    WAITING_TUN = "waiting_tun"
    PROBING = "probing"
    UPGRADING_AWG = "upgrading_awg"
    UP = "up"
    DEGRADED = "degraded"
    FAIL_CLOSED = "fail_closed"
    STOPPING = "stopping"

    @property
    def is_connecting(self) -> bool:
        return self in {
            Phase.PREPARING,
            Phase.LAUNCHING,
            Phase.WAITING_SOCKS,
            Phase.WAITING_TUN,
            Phase.PROBING,
            Phase.UPGRADING_AWG,
        }

    @property
    def holds_watchdog(self) -> bool:
        return self.is_connecting or self is Phase.STOPPING

    @property
    def is_up(self) -> bool:
        return self in {Phase.UP, Phase.DEGRADED, Phase.FAIL_CLOSED}


@dataclass(frozen=True)
class Snapshot:
    phase: Phase = Phase.IDLE
    dial: str = ""
    singbox_running: bool = False
    tun_running: bool = False
    tun_wanted: bool = False
    tun_ready: bool = False
    socks_up: bool = False
    http_up: bool = False
    pac_up: bool = False
    kill_switch: bool = False
    kill_switch_applied: bool = False
    exit_probe_error: str = ""
    exit_probe_hint: str = ""
    fail_closed: bool = False
    socks_scope: str = "full"
    server_target: str = ""
    socks_port: int = 1080
    http_port: int = 1088
    pac_port: int = 1089
    watchdog_running: bool = False
    reverse_ssh_running: bool = False
    reverse_ssh_listen: int = 2222
    hold_watchdog: bool = False
    pending_win_tun: bool = False
    pending_awg_tun: bool = False
    defer_win_ks: bool = False
    want_watchdog: bool = False
    pending_allow: tuple[str, ...] = ()
    reason: str = ""

    @property
    def connecting(self) -> bool:
        return self.phase.is_connecting or self.hold_watchdog or self.pending_win_tun

    @property
    def active(self) -> bool:
        return bool(self.singbox_running or self.tun_running or self.phase.is_up)

    def to_status_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
        data["connecting"] = self.connecting
        data["pending_allow"] = list(self.pending_allow)
        return data

    @classmethod
    def from_status_dict(cls, st: dict[str, Any]) -> Snapshot:
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in st.items():
            if key not in known:
                continue
            if key == "phase":
                try:
                    kwargs["phase"] = Phase(str(value))
                except ValueError:
                    kwargs["phase"] = Phase.IDLE
            elif key == "pending_allow":
                kwargs["pending_allow"] = tuple(str(x) for x in (value or ()))
            else:
                kwargs[key] = value
        return cls(**kwargs)
