"""Shim: kill-switch helpers live in desktop.killswitch."""

from __future__ import annotations

import sys

from desktop.killswitch import _impl as _impl

sys.modules[__name__] = _impl
