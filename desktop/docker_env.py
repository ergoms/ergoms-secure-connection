"""Docker / Compose helpers: HTTP proxy via host bridge + optional extra_hosts."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable

from desktop import procutil
from desktop.logutil import noop
from desktop.sys.constants import DOCKER_DESKTOP_HOST_GATEWAY, DOCKER_NO_PROXY

LogFn = Callable[[str], None]


# Common registries / CDNs that fail when container DNS is broken.
DEFAULT_DNS_HOSTS: tuple[str, ...] = (
    "pypi.org",
    "files.pythonhosted.org",
    "pypi.python.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "ghcr.io",
    "registry-1.docker.io",
    "auth.docker.io",
    "production.cloudflare.docker.com",
    "index.docker.io",
)

# Docker Desktop (Windows/Mac) host gateway — used if live detect fails.
_DOCKER_DESKTOP_HOST_FALLBACK = DOCKER_DESKTOP_HOST_GATEWAY

# getaddrinfo has no native timeout; under broken TUN DNS it can hang forever.
_RESOLVE_HOST_TIMEOUT = 2.0
_DOCKER_PROBE_TIMEOUT = 12.0


def resolve_hosts(hosts: list[str] | tuple[str, ...]) -> list[tuple[str, str]]:
    """Resolve hostnames on the Windows/Linux host (where DNS usually works)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for host in hosts:
            h = (host or "").strip().lower().rstrip(".")
            if not h or h in seen:
                continue
            seen.add(h)
            fut = pool.submit(
                socket.getaddrinfo, h, None, socket.AF_INET, socket.SOCK_STREAM
            )
            try:
                infos = fut.result(timeout=_RESOLVE_HOST_TIMEOUT)
            except (OSError, concurrent.futures.TimeoutError):
                continue
            ip = ""
            for info in infos:
                cand = info[4][0]
                if cand and not cand.startswith("127."):
                    ip = cand
                    break
            if ip:
                out.append((h, ip))
    return out


def _is_usable_host_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast:
        return False
    return True


def _docker_exe() -> str | None:
    return procutil.which_exe("docker")


def detect_docker_host_ip(
    *, log: LogFn = noop, timeout: float = _DOCKER_PROBE_TIMEOUT
) -> str | None:
    """IPv4 that containers can use to reach the host (Docker Desktop host-gateway).

    Container DNS is often broken, so proxy URL must be an IP, not a hostname.
    """
    from desktop.branding import ENV_DOCKER_HOST_IP, env

    override = env(ENV_DOCKER_HOST_IP)
    if override and _is_usable_host_ip(override):
        return override

    # Windows Docker Desktop host-gateway is always 192.168.65.254.
    # `docker info` cannot change that, flashes a console, and hangs for
    # seconds when the engine is down or still starting.
    if sys.platform == "win32":
        return _DOCKER_DESKTOP_HOST_FALLBACK

    docker = _docker_exe()
    if not docker:
        if sys.platform == "darwin":
            return _DOCKER_DESKTOP_HOST_FALLBACK
        return None

    # Fail fast when daemon is down/hung — do not block `on` for minutes.
    try:
        info = procutil.run(
            [docker, "info"],
            timeout=min(3.0, timeout),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"docker info: {exc} — skip host-ip probe")
        if sys.platform == "darwin":
            return _DOCKER_DESKTOP_HOST_FALLBACK
        return None
    if info.returncode != 0:
        err = (info.stderr or info.stdout or "").strip().splitlines()
        log(
            "docker info failed — skip host-ip probe"
            + (f": {err[-1][:160]}" if err else "")
        )
        if sys.platform == "darwin":
            return _DOCKER_DESKTOP_HOST_FALLBACK
        return None

    images = (
        "alpine:3.20",
        "alpine:latest",
        "busybox:1.36",
    )
    for image in images:
        try:
            r = procutil.run(
                [
                    docker,
                    "run",
                    "--rm",
                    "--add-host=opscontent-hdi:host-gateway",
                    image,
                    "getent",
                    "ahostsv4",
                    "opscontent-hdi",
                ],
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"docker host-ip probe ({image}): {exc}")
            continue
        if r.returncode != 0:
            # Image missing / docker not running — try next or fallback
            err = (r.stderr or r.stdout or "").strip().splitlines()
            if err:
                log(f"docker host-ip probe ({image}): {err[-1][:160]}")
            continue
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if parts and _is_usable_host_ip(parts[0]):
                return parts[0]

    if sys.platform == "darwin":
        log(f"docker host-ip detect failed — fallback {_DOCKER_DESKTOP_HOST_FALLBACK}")
        return _DOCKER_DESKTOP_HOST_FALLBACK
    return None


def write_docker_env(
    http_port: int,
    docker_env: Path,
    compose_path: Path,
    hosts_path: Path,
    *,
    dns_hosts: list[str] | None = None,
    active: bool = True,
    log: LogFn = noop,
    run_ps1: Path | None = None,
    run_sh: Path | None = None,
) -> None:
    """Write var/docker.env, compose override, hosts file, and run wrappers."""
    docker_env.parent.mkdir(parents=True, exist_ok=True)
    port = int(http_port)

    proxy_ip = detect_docker_host_ip(log=log) if active else None
    # Prefer IP so containers need zero DNS to reach the bridge.
    if proxy_ip:
        proxy_host = proxy_ip
    else:
        proxy_host = "host.docker.internal"
    proxy = f"http://{proxy_host}:{port}"
    base_noproxy = ",".join((*DOCKER_NO_PROXY, "192.168.0.0/16"))
    if proxy_ip:
        noproxy = f"{base_noproxy},{proxy_ip}"
    else:
        noproxy = base_noproxy

    if active:
        env_lines = [
            "# ERGOMS SECURE CONNECTION -> Docker: HTTP bridge on the host (no container DNS needed)",
            "# Proxy reaches Windows/Mac Docker Desktop via host-gateway IP.",
            f"# Usage: docker run --env-file {docker_env.as_posix()} IMAGE ...",
            f"# Or:    {(run_ps1 or docker_env.with_name('docker-run.ps1')).as_posix()} -- IMAGE ...",
            f"HTTP_PROXY={proxy}",
            f"HTTPS_PROXY={proxy}",
            f"http_proxy={proxy}",
            f"https_proxy={proxy}",
            f"ALL_PROXY={proxy}",
            f"NO_PROXY={noproxy}",
            f"no_proxy={noproxy}",
            "",
        ]
        if proxy_ip:
            env_lines.insert(
                4,
                f"# Detected docker host IP: {proxy_ip} (override: ERGOMS_SC_DOCKER_HOST_IP)",
            )
    else:
        env_lines = [
            "# ERGOMS SECURE CONNECTION -> Docker: relay OFF - unset proxies in containers",
            "# Run: ergoms-secure-connection on   then: ergoms-secure-connection docker-env",
            "",
        ]

    docker_env.write_text("\n".join(env_lines), encoding="utf-8")

    host_list = list(dns_hosts) if dns_hosts is not None else list(DEFAULT_DNS_HOSTS)
    resolved = resolve_hosts(host_list) if active else []
    if active and proxy_ip:
        # Ensure host.docker.internal works even when Engine omits it (broken DNS).
        resolved = [("host.docker.internal", proxy_ip), *resolved]

    hosts_lines = [
        "# ERGOMS SECURE CONNECTION DOCKER_DNS_FIX — IP resolved on the host",
        "# docker run --add-host=name:ip …  or compose extra_hosts",
    ]
    for name, ip in resolved:
        hosts_lines.append(f"{ip}\t{name}")
    hosts_lines.append("")
    hosts_path.write_text("\n".join(hosts_lines), encoding="utf-8")

    if active:
        compose = _compose_snippet(proxy, noproxy, resolved, compose_path, proxy_ip)
    else:
        compose = (
            "# ERGOMS SECURE CONNECTION — relay OFF\n"
            "# Run: ergoms-secure-connection on && ergoms-secure-connection docker-env\n"
        )
    compose_path.write_text(compose, encoding="utf-8")

    ps1 = run_ps1 or docker_env.with_name("docker-run.ps1")
    sh = run_sh or docker_env.with_name("docker-run.sh")
    _write_run_wrappers(ps1, sh, docker_env, hosts_path, active=active, proxy_ip=proxy_ip)

    if active:
        log(
            f"Docker helper: {docker_env.name} proxy={proxy} "
            f"extra_hosts={len(resolved)}"
        )
    else:
        log(f"Docker helper cleared ({docker_env.name})")


def _compose_snippet(
    proxy: str,
    noproxy: str,
    resolved: list[tuple[str, str]],
    compose_path: Path,
    proxy_ip: str | None,
) -> str:
    lines = [
        "# ERGOMS SECURE CONNECTION — merge into your compose project:",
        f"#   docker compose -f docker-compose.yml -f {compose_path.as_posix()} up",
        "#",
        "# Attach the anchor to services that need outbound HTTP(S):",
        "#   services:",
        "#     app:",
        "#       <<: *ergoms-secure-connection-proxy",
        "#",
        "x-ergoms-secure-connection-proxy: &ergoms-secure-connection-proxy",
        "  extra_hosts:",
        '    - "host.docker.internal:host-gateway"',
    ]
    if proxy_ip:
        lines.append(f'    - "host.docker.internal:{proxy_ip}"')
    for name, ip in resolved:
        if name == "host.docker.internal":
            continue
        lines.append(f'    - "{name}:{ip}"')
    lines.extend(
        [
            "  environment:",
            f'    HTTP_PROXY: "{proxy}"',
            f'    HTTPS_PROXY: "{proxy}"',
            f'    http_proxy: "{proxy}"',
            f'    https_proxy: "{proxy}"',
            f'    ALL_PROXY: "{proxy}"',
            f'    NO_PROXY: "{noproxy}"',
            f'    no_proxy: "{noproxy}"',
            "",
        ]
    )
    return "\n".join(lines)


def _write_run_wrappers(
    ps1: Path,
    sh: Path,
    docker_env: Path,
    hosts_path: Path,
    *,
    active: bool,
    proxy_ip: str | None,
) -> None:
    env_posix = docker_env.as_posix()
    hosts_posix = hosts_path.as_posix()
    add_host = (
        f"--add-host=host.docker.internal:{proxy_ip}"
        if proxy_ip
        else "--add-host=host.docker.internal:host-gateway"
    )

    if active:
        ps1.write_text(
            "\r\n".join(
                [
                    "# ERGOMS SECURE CONNECTION: docker run with host proxy (no container DNS)",
                    f"# Usage: .\\var\\docker-run.ps1 -- IMAGE [args…]",
                    f"#    or: .\\var\\docker-run.ps1 -AddHosts -- IMAGE …",
                    "param(",
                    "  [switch]$AddHosts,",
                    "  [Parameter(ValueFromRemainingArguments = $true)]",
                    "  [string[]]$DockerArgs",
                    ")",
                    f"$envFile = '{docker_env}'",
                    f"$addHost = '{add_host}'",
                    "$extra = @('--env-file', $envFile, $addHost)",
                    "if ($AddHosts -and (Test-Path '" + str(hosts_path) + "')) {",
                    f"  Get-Content -LiteralPath '{hosts_path}' | ForEach-Object {{",
                    "    $line = $_.Trim()",
                    "    if (-not $line -or $line.StartsWith('#')) { return }",
                    "    $parts = $line -split '\\s+', 2",
                    "    if ($parts.Count -ge 2) { $extra += @('--add-host', ($parts[1] + ':' + $parts[0])) }",
                    "  }",
                    "}",
                    "docker run @extra @DockerArgs",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        sh.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "# ERGOMS SECURE CONNECTION: docker run with host proxy (no container DNS)",
                    f"# Usage: {sh.as_posix()} [--add-hosts] -- IMAGE [args…]",
                    "set -euo pipefail",
                    f'ENV_FILE="{env_posix}"',
                    f'ADD_HOST="{add_host}"',
                    "ADD_HOSTS=0",
                    'if [[ "${1:-}" == "--add-hosts" ]]; then ADD_HOSTS=1; shift; fi',
                    'if [[ "${1:-}" == "--" ]]; then shift; fi',
                    'extra=(--env-file "$ENV_FILE" "$ADD_HOST")',
                    f'if [[ "$ADD_HOSTS" == "1" && -f "{hosts_posix}" ]]; then',
                    "  while read -r ip name _; do",
                    '    [[ -z "${ip:-}" || "${ip:0:1}" == "#" ]] && continue',
                    '    [[ -n "${name:-}" ]] && extra+=(--add-host "${name}:${ip}")',
                    f'  done < "{hosts_posix}"',
                    "fi",
                    'exec docker run "${extra[@]}" "$@"',
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
    else:
        ps1.write_text(
            "# ERGOMS SECURE CONNECTION docker-run: relay OFF — run ergoms-secure-connection on first\r\n",
            encoding="utf-8",
        )
        sh.write_text(
            "#!/usr/bin/env bash\necho 'ERGOMS SECURE CONNECTION relay OFF — run: ergoms-secure-connection on' >&2\nexit 1\n",
            encoding="utf-8",
            newline="\n",
        )


def docker_dns_probe(*, log: LogFn = noop, timeout: float = 60.0) -> int:
    """Return 0 if a bridge container can resolve pypi.org (needs TUN hijack)."""
    docker = _docker_exe()
    if not docker:
        log("docker not found")
        return 2
    log("docker-dns: getent hosts pypi.org (bridge)")
    try:
        r = procutil.run(
            [
                docker,
                "run",
                "--rm",
                "--network",
                "bridge",
                "alpine:3.20",
                "getent",
                "ahostsv4",
                "pypi.org",
            ],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"docker-dns failed: {exc}")
        return 1
    line = ((r.stdout or "").strip().splitlines() or [""])[0][:120]
    if r.returncode == 0 and line:
        log(f"docker-dns OK: {line}")
        return 0
    err = ((r.stderr or r.stdout or "").strip().splitlines() or [""])[-1][:160]
    if err:
        log(err)
    log(
        "docker-dns FAILED — поднимите TUN (TUN=1 / tun-on). "
        "Без него используйте HTTP_PROXY из var/docker.env"
    )
    return 1


def docker_smoke_test(
    http_port: int,
    *,
    log: LogFn = noop,
    proxy_ip: str | None = None,
) -> int:
    """Return 0 if a container can HTTPS via the host bridge (and report DNS)."""
    docker = _docker_exe()
    if not docker:
        log("docker not found")
        return 2
    ip = proxy_ip or detect_docker_host_ip(log=log)
    if not ip:
        log("could not detect docker host IP")
        return 2
    # DNS is independent of the proxy path; report but don't fail the smoke test.
    dns_rc = docker_dns_probe(log=log)
    proxy = f"http://{ip}:{int(http_port)}"
    log(f"docker-test: curl https://pypi.org via {proxy}")
    try:
        r = procutil.run(
            [
                docker,
                "run",
                "--rm",
                "-e",
                f"HTTP_PROXY={proxy}",
                "-e",
                f"HTTPS_PROXY={proxy}",
                "curlimages/curl:8.5.0",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "http=%{http_code}\n",
                "-I",
                "--connect-timeout",
                "25",
                "https://pypi.org",
            ],
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"docker-test failed: {exc}")
        return 1
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if out:
        log(out.splitlines()[-1][:200])
    if r.returncode == 0 and "http=200" in (r.stdout or ""):
        if dns_rc == 0:
            log("docker-test OK (proxy + DNS)")
        else:
            log("docker-test OK (proxy); DNS still broken without TUN")
        return 0
    log(f"docker-test FAILED exit={r.returncode}")
    return 1
