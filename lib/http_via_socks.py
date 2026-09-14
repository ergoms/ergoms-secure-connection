"""Compatibility shim — live PAC helpers live in lib.pac."""

from lib.pac import BypassMatcher, build_pac, bypass_to_singbox

__all__ = ["BypassMatcher", "build_pac", "bypass_to_singbox"]
