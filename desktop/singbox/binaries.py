"""Locate / download official and AWG sing-box binaries via TunManager."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from desktop.logutil import noop
from desktop.tun import TunManager

LogFn = Callable[[str], None]


class SingboxBinaries:
    def __init__(
        self,
        var_dir: Path,
        tools_dir: Path,
        logs_dir: Path,
        log: LogFn = noop,
    ) -> None:
        self._helper = TunManager(var_dir, tools_dir, logs_dir, log=log)

    def find_sing_box(self, explicit: str = "") -> Path | None:
        return self._helper.find_sing_box(explicit)

    def find_awg_sing_box(self) -> Path | None:
        return self._helper.find_awg_sing_box()

    def awg_version_ok(self) -> bool:
        return self._helper.awg_version_ok()

    def ensure_downloaded(self, proxy_url: str | None = None) -> Path:
        return self._helper.ensure_downloaded(proxy_url=proxy_url)

    def ensure_awg_downloaded(self, proxy_url: str | None = None) -> Path:
        return self._helper.ensure_awg_downloaded(proxy_url=proxy_url)
