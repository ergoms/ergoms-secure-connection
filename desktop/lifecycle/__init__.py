"""Explicit VPN connection lifecycle: actions, phases, session, plan, pipeline."""

from desktop.lifecycle.actions import Action
from desktop.lifecycle.plan import ConnectPlan, resolve_connect_plan
from desktop.lifecycle.session import ConnectionSession
from desktop.lifecycle.snapshot import Phase, Snapshot

__all__ = [
    "Action",
    "ConnectPlan",
    "ConnectionSession",
    "Phase",
    "Snapshot",
    "resolve_connect_plan",
]
