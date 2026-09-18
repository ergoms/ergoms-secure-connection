"""Live hints: domain ↔ IP and process/service peers."""

from __future__ import annotations

import concurrent.futures
import socket
from typing import Any

from desktop.proc_net import (
    Peer,
    collect_peers,
    looks_like_file,
    resolve_service_image,
)
from desktop.route_tokens import (
    KIND_DOMAIN,
    KIND_IP,
    KIND_PROCESS,
    KIND_SERVICE,
    KIND_UNKNOWN,
    PREFIX_EXE,
    PREFIX_SVC,
    RouteToken,
    classify_input,
    is_shared_process,
    looks_like_ip,
    parse_token,
)

_RESOLVE_TIMEOUT = 1.6
_REVERSE_TIMEOUT = 0.8
_MAX_IPS = 8
_MAX_PEER_LOOKUPS = 24


def analyze_token(query: str) -> dict[str, Any]:
    raw = (query or "").strip().strip('"')
    payload: dict[str, Any] = {
        "query": raw,
        "kind": KIND_UNKNOWN,
        "ambiguous": False,
        "token": raw,
        "label": raw,
        "ips": [],
        "domains": [],
        "peers": [],
        "warning": "",
        "shared": False,
    }
    if not raw:
        return payload
    kind, ambiguous = classify_input(raw)
    tok = parse_token(raw)
    payload["kind"] = tok.kind if tok and tok.kind != KIND_UNKNOWN else kind
    payload["ambiguous"] = ambiguous or (tok is not None and tok.kind == KIND_UNKNOWN)
    if tok is not None:
        payload["token"] = tok.serialize() if tok.kind != KIND_UNKNOWN else tok.value
        payload["label"] = tok.label
        payload["shared"] = is_shared_process(tok.value) if tok.kind == KIND_PROCESS else False
    host = tok.value if tok else raw
    if looks_like_ip(host) or payload["kind"] == KIND_IP:
        return _analyze_ip(host, payload)
    if payload["kind"] == KIND_DOMAIN:
        return _analyze_domain(tok.value if tok else raw, payload)
    if payload["kind"] == KIND_PROCESS:
        return _analyze_process(tok, payload)
    if payload["kind"] == KIND_SERVICE:
        return _analyze_service(tok, payload)
    return payload


def analyze_process(*, pid: int = 0, name: str = "", path: str = "") -> dict[str, Any]:
    label = path or name or (f"pid {pid}" if pid else "")
    token = f"{PREFIX_EXE}{path or name}" if (path or name) else ""
    payload: dict[str, Any] = {
        "query": label,
        "kind": KIND_PROCESS,
        "ambiguous": False,
        "token": token,
        "label": path or name or label,
        "ips": [],
        "domains": [],
        "peers": [],
        "warning": "",
        "shared": is_shared_process(path or name),
    }
    if payload["shared"]:
        payload["warning"] = (
            "Общий процесс Windows — в маршрут его не ставим, только найденные адреса."
        )
        payload["token"] = ""
    return _fill_peers(payload, pid=pid, name=name or path)


def analyze_service(name: str) -> dict[str, Any]:
    info = resolve_service_image(name)
    svc = (name or "").strip()
    if svc.lower().startswith(PREFIX_SVC):
        svc = svc[len(PREFIX_SVC) :].strip()
    payload: dict[str, Any] = {
        "query": svc,
        "kind": KIND_SERVICE,
        "ambiguous": False,
        "token": f"{PREFIX_SVC}{svc}" if svc else "",
        "label": (info.display if info else svc) or svc,
        "ips": [],
        "domains": [],
        "peers": [],
        "warning": "",
        "shared": bool(info.shared) if info else False,
        "exe": info.exe if info else "",
        "path": info.path if info else "",
    }
    if payload["shared"]:
        payload["warning"] = (
            "Служба крутится в svchost — добавьте найденные IP и домены, не весь процесс."
        )
        payload["token"] = ""
    return _fill_peers(payload, service=svc)


def resolve_ips(host: str) -> list[str]:
    name = (host or "").strip().rstrip(".")
    if not name:
        return []
    if looks_like_ip(name):
        return [name.strip("[]")]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                socket.getaddrinfo, name, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            infos = fut.result(timeout=_RESOLVE_TIMEOUT)
    except (OSError, concurrent.futures.TimeoutError):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = info[4][0]
        if not ip or ip in seen:
            continue
        seen.add(ip)
        out.append(ip)
        if len(out) >= _MAX_IPS:
            break
    return out


def reverse_name(ip: str) -> str:
    addr = (ip or "").strip().strip("[]")
    if not addr or not looks_like_ip(addr):
        return ""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(socket.gethostbyaddr, addr)
            name, _aliases, _ = fut.result(timeout=_REVERSE_TIMEOUT)
    except (OSError, concurrent.futures.TimeoutError):
        return ""
    return str(name or "").strip().rstrip(".").lower()


def _analyze_ip(raw: str, payload: dict[str, Any]) -> dict[str, Any]:
    ip = raw.strip().strip("[]")
    payload["kind"] = KIND_IP
    payload["token"] = ip
    payload["label"] = ip
    payload["ips"] = [ip]
    name = reverse_name(ip)
    if name:
        payload["domains"] = [name]
    return payload


def _analyze_domain(host: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = (host or "").strip().lower().rstrip(".")
    payload["kind"] = KIND_DOMAIN
    payload["token"] = name
    payload["label"] = name
    payload["domains"] = [name]
    payload["ips"] = resolve_ips(name)
    return payload


def _analyze_process(tok: RouteToken | None, payload: dict[str, Any]) -> dict[str, Any]:
    value = tok.value if tok else payload.get("label") or ""
    if is_shared_process(value):
        payload["shared"] = True
        payload["warning"] = (
            "Общий процесс Windows — в маршрут его не ставим, только найденные адреса."
        )
        payload["token"] = ""
    name = value
    path = value if looks_like_file(value) else ""
    return _fill_peers(payload, name=name, path=path)


def _analyze_service(tok: RouteToken | None, payload: dict[str, Any]) -> dict[str, Any]:
    name = tok.value if tok else payload.get("label") or ""
    return analyze_service(name)


def _fill_peers(
    payload: dict[str, Any],
    *,
    pid: int = 0,
    name: str = "",
    path: str = "",
    service: str = "",
) -> dict[str, Any]:
    peers = collect_peers(pid=pid, name=path or name, service=service)
    enriched = _enrich_peers(peers)
    payload["peers"] = enriched
    ips = [p["ip"] for p in enriched if p.get("ip")]
    domains = [p["domain"] for p in enriched if p.get("domain")]
    payload["ips"] = list(dict.fromkeys(ips))
    payload["domains"] = list(dict.fromkeys(domains))
    if not enriched and not payload.get("warning"):
        payload["warning"] = "Сейчас нет внешних подключений — оставьте процесс или подождите."
    return payload


def _enrich_peers(peers: list[Peer]) -> list[dict[str, Any]]:
    sample = peers[:_MAX_PEER_LOOKUPS]
    resolved: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(reverse_name, peer.ip): peer.ip for peer in sample}
        for fut in concurrent.futures.as_completed(futs):
            ip = futs[fut]
            try:
                resolved[ip] = fut.result()
            except Exception:  # noqa: BLE001
                resolved[ip] = ""
    out: list[dict[str, Any]] = []
    for peer in sample:
        item = peer.as_dict()
        item["domain"] = resolved.get(peer.ip, "")
        out.append(item)
    return out
