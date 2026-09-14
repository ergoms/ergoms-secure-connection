"""Single elevation handoff for GUI and CLI."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from desktop import procutil


class ElevationService:
    def __init__(self, client: Any) -> None:
        self.client = client

    def needed(self, action: str) -> bool:
        needs = getattr(self.client, "needs_elevation", None)
        return bool(callable(needs) and needs(action=action))

    def relaunch(self, args: Sequence[str], *, cwd: str | Path) -> bool:
        return bool(procutil.relaunch_as_admin(list(args), cwd=str(cwd)))
