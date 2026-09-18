"""Mixed route tokens: domain, IP, process, Windows service."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


KIND_DOMAIN = "domain"
KIND_IP = "ip"
KIND_PROCESS = "process"
KIND_SERVICE = "service"
KIND_UNKNOWN = "unknown"

PREFIX_EXE = "exe:"
PREFIX_SVC = "svc:"

SHARED_PROCESS_NAMES = frozenset(
    {
        "svchost.exe",
        "svchost",
        "services.exe",
        "lsass.exe",
        "csrss.exe",
        "smss.exe",
        "wininit.exe",
        "system",
    }
)

_SINGLE_LABEL_DOMAINS = frozenset({"localhost", "local", "intranet"})


@dataclass(frozen=True)
class RouteToken:
    kind: str
    value: str

    def serialize(self) -> str:
        if self.kind == KIND_SERVICE:
            raw = self.value
            return raw if raw.lower().startswith(PREFIX_SVC) else f"{PREFIX_SVC}{raw}"
        if self.kind == KIND_PROCESS:
            raw = self.value
            if raw.lower().startswith(PREFIX_EXE):
                return raw
            return f"{PREFIX_EXE}{raw}"
        return self.value

    @property
    def label(self) -> str:
        if self.kind == KIND_SERVICE:
            return _strip_prefix(self.value, PREFIX_SVC)
        if self.kind == KIND_PROCESS:
            return _strip_prefix(self.value, PREFIX_EXE)
        return self.value


@dataclass(frozen=True)
class ParsedRoutes:
    domains: list[str]
    suffixes: list[str]
    ips: list[str]
    processes: list[str]
    services: list[str]


def _strip_prefix(raw: str, prefix: str) -> str:
    text = (raw or "").strip()
    if text.lower().startswith(prefix):
        return text[len(prefix) :].strip()
    return text


def extract_url_host(raw: str) -> str | None:
    """Host from http(s) URL, or None if this is not a web URL."""
    text = (raw or "").strip().strip('"')
    if not text:
        return None
    low = text.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return None
    host = (urlparse(text).hostname or "").strip().rstrip(".")
    return host or None


def normalize_route_input(raw: str) -> str:
    """Strip quotes and pull a bare host out of https://example.com/path."""
    text = (raw or "").strip().strip('"')
    return extract_url_host(text) or text


def canonical_route_token(raw: str) -> str:
    """Value stored in the rules list (domain host, not the pasted URL)."""
    tok = parse_token(raw)
    if tok is None:
        return ""
    if tok.kind in (KIND_DOMAIN, KIND_IP):
        return tok.value
    return tok.serialize()


def looks_like_ip(raw: str) -> bool:
    text = (raw or "").strip().strip("[]")
    if not text:
        return False
    try:
        if "/" in text:
            ipaddress.ip_network(text, strict=False)
            return True
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def looks_like_path(raw: str) -> bool:
    text = (raw or "").strip().strip('"')
    if extract_url_host(text):
        return False
    return "\\" in text or "/" in text


def looks_like_process_name(raw: str) -> bool:
    text = (raw or "").strip().strip('"')
    if not text:
        return False
    low = text.lower()
    if low.startswith(PREFIX_EXE):
        return True
    if looks_like_path(text):
        return True
    return low.endswith(".exe")


def is_shared_process(raw: str) -> bool:
    name = _strip_prefix((raw or "").strip().strip('"'), PREFIX_EXE)
    base = name.replace("\\", "/").split("/")[-1].lower()
    return base in SHARED_PROCESS_NAMES


def is_host_pattern(raw: str) -> bool:
    """Domain, glob, or IP — not an exe/service token."""
    text = normalize_route_input(raw)
    if not text:
        return False
    low = text.lower()
    if low.startswith(PREFIX_EXE) or low.startswith(PREFIX_SVC):
        return False
    if looks_like_path(text) or low.endswith(".exe"):
        return False
    return True


def host_patterns(items: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items or []:
        text = normalize_route_input(str(raw or "").strip())
        if not text or not is_host_pattern(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def as_cidr(raw: str) -> str:
    text = (raw or "").strip().strip("[]")
    if "/" in text:
        return text
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return text
    return f"{addr}/128" if addr.version == 6 else f"{addr}/32"


def classify_input(raw: str) -> tuple[str, bool]:
    """Return (kind, ambiguous). Ambiguous names can be domain or process."""
    text = normalize_route_input(raw)
    if not text:
        return KIND_UNKNOWN, False
    low = text.lower()
    if low.startswith(PREFIX_SVC):
        return KIND_SERVICE, False
    if looks_like_process_name(text):
        return KIND_PROCESS, False
    if looks_like_ip(text):
        return KIND_IP, False
    if "*" in text or "?" in text or "." in text or low in _SINGLE_LABEL_DOMAINS:
        return KIND_DOMAIN, False
    return KIND_UNKNOWN, True


def parse_token(raw: str) -> RouteToken | None:
    text = normalize_route_input(raw)
    if not text:
        return None
    low = text.lower()
    if low.startswith(PREFIX_SVC):
        name = text[len(PREFIX_SVC) :].strip()
        if not name:
            return None
        return RouteToken(KIND_SERVICE, name)
    if low.startswith(PREFIX_EXE):
        name = text[len(PREFIX_EXE) :].strip().strip('"')
        if not name:
            return None
        return RouteToken(KIND_PROCESS, name)
    if looks_like_process_name(text):
        return RouteToken(KIND_PROCESS, text)
    if looks_like_ip(text):
        return RouteToken(KIND_IP, text.strip("[]"))
    if looks_like_path(text):
        return RouteToken(KIND_PROCESS, text)
    return RouteToken(KIND_DOMAIN, text.lower())


def parse_list(items: Iterable[str] | None) -> list[RouteToken]:
    out: list[RouteToken] = []
    seen: set[tuple[str, str]] = set()
    for raw in items or []:
        tok = parse_token(str(raw))
        if tok is None or tok.kind == KIND_UNKNOWN:
            continue
        key = (tok.kind, tok.value.lower() if tok.kind != KIND_PROCESS else tok.value.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def serialize_list(tokens: Iterable[RouteToken]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        text = tok.serialize()
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def parse_routes(items: Iterable[str] | None) -> ParsedRoutes:
    domains: list[str] = []
    suffixes: list[str] = []
    ips: list[str] = []
    processes: list[str] = []
    services: list[str] = []
    seen_d: set[str] = set()
    seen_s: set[str] = set()
    seen_i: set[str] = set()
    seen_p: set[str] = set()
    seen_svc: set[str] = set()
    for tok in parse_list(items):
        if tok.kind == KIND_DOMAIN:
            value = tok.value.lower()
            if value.startswith("*."):
                suf = value[1:]
                if suf not in seen_s:
                    seen_s.add(suf)
                    suffixes.append(suf)
            elif "*" not in value and "?" not in value:
                if value not in seen_d:
                    seen_d.add(value)
                    domains.append(value)
        elif tok.kind == KIND_IP:
            cidr = as_cidr(tok.value)
            if cidr not in seen_i:
                seen_i.add(cidr)
                ips.append(cidr)
        elif tok.kind == KIND_PROCESS:
            key = tok.value.lower()
            if key not in seen_p:
                seen_p.add(key)
                processes.append(tok.value)
        elif tok.kind == KIND_SERVICE:
            key = tok.value.lower()
            if key not in seen_svc:
                seen_svc.add(key)
                services.append(tok.value)
    return ParsedRoutes(domains, suffixes, ips, processes, services)


def process_matchers(value: str) -> tuple[list[str], list[str]]:
    """Split a process token into sing-box process_name / process_path lists."""
    name = _strip_prefix((value or "").strip().strip('"'), PREFIX_EXE)
    if not name:
        return [], []
    if is_shared_process(name):
        return [], []
    if looks_like_path(name):
        return [], [name]
    names = [name]
    if not name.lower().endswith(".exe"):
        names.append(f"{name}.exe")
    return names, []


def tokens_json(items: Iterable[str] | None) -> str:
    return json.dumps([str(x).strip() for x in (items or []) if str(x).strip()], ensure_ascii=False)


def tokens_from_json(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "").strip()
    if not text or text == "[]":
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [x.strip() for x in text.split(",") if x.strip()]
    if isinstance(parsed, list):
        return [str(x).strip() for x in parsed if str(x).strip()]
    return []


def token_payload(raw: str) -> dict[str, Any]:
    tok = parse_token(raw)
    kind, ambiguous = classify_input(raw)
    if tok is None:
        return {
            "raw": raw,
            "kind": kind,
            "label": (raw or "").strip(),
            "token": (raw or "").strip(),
            "ambiguous": ambiguous,
            "shared": False,
        }
    return {
        "raw": raw,
        "kind": tok.kind,
        "label": tok.label,
        "token": tok.serialize(),
        "ambiguous": ambiguous,
        "shared": is_shared_process(tok.value) if tok.kind == KIND_PROCESS else False,
    }
