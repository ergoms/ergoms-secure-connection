"""PAC generation and proxy_bypass → sing-box conversion."""

from __future__ import annotations

import fnmatch


class BypassMatcher:
    """Precompiled bypass host patterns (exact / suffix / glob)."""

    __slots__ = ("_exact", "_suffixes", "_globs")

    def __init__(self, patterns: list[str]) -> None:
        exact: set[str] = set()
        suffixes: list[str] = []
        globs: list[str] = []
        for raw in patterns:
            p = raw.strip().lower()
            if not p:
                continue
            if "*" in p or "?" in p:
                globs.append(p)
            else:
                exact.add(p)
                suffixes.append("." + p)
        self._exact = exact
        self._suffixes = suffixes
        self._globs = globs

    def matches(self, host: str) -> bool:
        h = (host or "").strip().lower().strip("[]").rstrip(".")
        if not h:
            return False
        if h in self._exact:
            return True
        for suf in self._suffixes:
            if h.endswith(suf):
                return True
        for p in self._globs:
            if fnmatch.fnmatch(h, p):
                return True
        return False


def bypass_to_singbox(patterns: list[str]) -> tuple[list[str], list[str]]:
    """Convert proxy_bypass patterns to sing-box domain_suffix / domain lists."""
    suffixes: set[str] = set()
    domains: set[str] = set()
    for raw in patterns:
        p = raw.strip().lower()
        if not p:
            continue
        if p.startswith("*."):
            suffixes.add(p[1:])
        elif "*" not in p and "?" not in p:
            domains.add(p)
        elif p.count("*") == 1 and p.startswith("*."):
            suffixes.add("." + p[2:])
    return sorted(suffixes), sorted(domains)


def _pac_cond(pattern: str) -> str:
    p = pattern.strip().lower()
    if not p:
        return ""
    if "*" in p or "?" in p:
        return f'shExpMatch(host, "{p}")'
    return f'(host == "{p}" || shExpMatch(host, "*.{p}"))'


def build_pac(
    listen_port: int,
    mode: str,
    tunnel_hosts: list[str],
    bypass_hosts: list[str],
    fallback: str,
    bypass_via: str,
) -> bytes:
    proxy = f"PROXY 127.0.0.1:{listen_port}"
    fb = f"PROXY {fallback}" if fallback else "DIRECT"
    if bypass_via == "corporate" and fallback:
        bypass_ret = f"PROXY {fallback}"
    else:
        bypass_ret = "DIRECT"

    bypass_conds = [_pac_cond(p) for p in bypass_hosts]
    bypass_conds = [c for c in bypass_conds if c]
    private_parts = [
        'host == "127.0.0.1"',
        'host == "localhost"',
        'host == "::1"',
        "isPlainHostName(host)",
        'shExpMatch(host, "10.*")',
        'shExpMatch(host, "192.168.*")',
        'shExpMatch(host, "169.254.*")',
        'dnsDomainIs(host, ".local")',
        'dnsDomainIs(host, ".internal")',
        'dnsDomainIs(host, ".localhost")',
        'shExpMatch(host, "metadata*")',
    ]
    for n in range(16, 32):
        private_parts.append(f'shExpMatch(host, "172.{n}.*")')
    private = " ||\n        ".join(private_parts)
    bypass_body = private
    if bypass_conds:
        bypass_body += " ||\n        " + " ||\n        ".join(bypass_conds)

    if mode == "full":
        pac = f"""function FindProxyForURL(url, host) {{
    host = host.toLowerCase();
    if ({bypass_body})
        return "{bypass_ret}";
    return "{proxy}";
}}
"""
    else:
        tunnel_conds = [_pac_cond(p) for p in tunnel_hosts]
        tunnel_conds = [c for c in tunnel_conds if c]
        if not tunnel_conds:
            tunnel_conds = [
                'host == "github.com" || shExpMatch(host, "*.github.com")',
                'shExpMatch(host, "*.githubusercontent.com")',
                'shExpMatch(host, "*.githubassets.com")',
                'host == "cursor.com" || shExpMatch(host, "*.cursor.com")',
                'shExpMatch(host, "*.cursor.sh")',
                'shExpMatch(host, "*.cursor-cdn.com")',
                'shExpMatch(host, "*.cursorapi.com")',
                'shExpMatch(host, "*.cursorvm.com")',
            ]
        tunnel_body = " ||\n        ".join(tunnel_conds)
        pac = f"""function FindProxyForURL(url, host) {{
    host = host.toLowerCase();
    if ({bypass_body})
        return "{bypass_ret}";
    if ({tunnel_body})
        return "{proxy}";
    return "{fb}";
}}
"""
    return pac.encode("utf-8")
