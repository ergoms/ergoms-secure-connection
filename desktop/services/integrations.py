"""PAC / git / Docker teardown wrapper."""

from __future__ import annotations

from typing import Any


class IntegrationService:
    def __init__(self, client: Any) -> None:
        self.client = client

    def teardown_if_dirty(self) -> None:
        self.client.teardown_overrides_if_dirty()
