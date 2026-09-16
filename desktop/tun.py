"""Shim: networking helpers live in desktop.net."""

from __future__ import annotations

import sys

from desktop.net import _impl as _impl

sys.modules[__name__] = _impl
