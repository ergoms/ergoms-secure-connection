"""Shared no-op logger default for optional `log=` callbacks."""

from __future__ import annotations


def noop(_msg: str = "") -> None:
    pass
