"""Single writer for connection snapshots. Every phase change is logged."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from desktop.lifecycle.actions import Action
from desktop.lifecycle.snapshot import Phase, Snapshot
from desktop.logutil import noop

LogFn = Callable[[str], None]
Observer = Callable[[Snapshot], None]


class SessionBusy(RuntimeError):
    """Another connect/disconnect is already running."""


class ConnectionSession:
    def __init__(self, *, log: LogFn = noop, op_timeout: float = 180.0) -> None:
        self._log = log
        self._lock = threading.RLock()
        self._snapshot = Snapshot()
        self._observers: list[Observer] = []
        self._op: Action | None = None
        self._op_started = 0.0
        self._op_timeout = op_timeout

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    @property
    def in_operation(self) -> bool:
        with self._lock:
            return self._op is not None

    @property
    def current_action(self) -> Action | None:
        with self._lock:
            return self._op

    def add_observer(self, fn: Observer) -> None:
        self._observers.append(fn)

    def transition(self, phase: Phase, reason: str, **updates: Any) -> Snapshot:
        with self._lock:
            old = self._snapshot
            snap = replace(old, phase=phase, reason=reason, **updates)
            self._snapshot = snap
            if old.phase is not phase:
                self._log(f"фаза {old.phase.value} → {phase.value}: {reason}")
        self._notify(snap)
        return snap

    def update(self, **updates: Any) -> Snapshot:
        with self._lock:
            snap = replace(self._snapshot, **updates)
            self._snapshot = snap
        self._notify(snap)
        return snap

    def reset_idle(self, reason: str = "отключено") -> Snapshot:
        return self.transition(Phase.IDLE, reason)

    @contextmanager
    def operation(self, action: Action) -> Iterator[Action]:
        with self._lock:
            if self._op is not None:
                elapsed = time.monotonic() - self._op_started
                if elapsed < self._op_timeout:
                    raise SessionBusy(f"уже выполняется {self._op.value}")
                self._log(
                    f"фаза: операция {self._op.value} зависла ({elapsed:.0f}s) — снимаю"
                )
            self._op = action
            self._op_started = time.monotonic()
        try:
            yield action
        finally:
            with self._lock:
                if self._op is action:
                    self._op = None
                    self._op_started = 0.0

    def _notify(self, snap: Snapshot) -> None:
        for fn in list(self._observers):
            try:
                fn(snap)
            except Exception:  # noqa: BLE001
                pass
