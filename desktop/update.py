"""Check GitHub Releases and apply a downloaded installer/tarball."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktop import __version__, procutil
from desktop.branding import ENV_GITHUB_REPO, ENV_GITHUB_TOKEN, env
from desktop.paths import data_root, gui_command, is_frozen

DEFAULT_GITHUB_REPO = "ergoms/ergoms-secure-connection"
WIN_ASSET_SUFFIX = "windows-x64-setup.exe"
LINUX_ASSET_SUFFIX = "linux-x64.tar.gz"
INNO_SILENT_ARGS = ("/SILENT", "/NORESTART", "/MERGETASKS=removeold,!wipeconfigs")
_GITHUB_JSON = "application/vnd.github+json"
_GITHUB_ASSET = "application/octet-stream"
_token_cache: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    html_url: str
    asset_name: str
    asset_url: str
    asset_api_url: str = ""


@dataclass(frozen=True)
class CheckResult:
    release: ReleaseInfo | None = None
    current_is_latest: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        info = self.release
        return {
            "error": self.error,
            "current_is_latest": self.current_is_latest,
            "release": None
            if info is None
            else {
                "version": info.version,
                "tag": info.tag,
                "html_url": info.html_url,
                "asset_name": info.asset_name,
                "asset_url": info.asset_url,
                "asset_api_url": info.asset_api_url,
            },
        }


def github_repo() -> str:
    return env(ENV_GITHUB_REPO, DEFAULT_GITHUB_REPO) or DEFAULT_GITHUB_REPO


def github_token() -> str:
    global _token_cache
    if _token_cache is not None:
        return _token_cache
    found = (
        env(ENV_GITHUB_TOKEN)
        or (os.environ.get("GITHUB_TOKEN") or "").strip()
        or (os.environ.get("GH_TOKEN") or "").strip()
    )
    if not found:
        try:
            found = (data_root() / "github.token").read_text(encoding="utf-8").strip()
        except OSError:
            found = ""
    if not found:
        gh = shutil.which("gh")
        if gh:
            result = procutil.run([gh, "auth", "token"], timeout=8)
            if result.returncode == 0:
                found = (result.stdout or "").strip()
    _token_cache = found
    return found


def is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError) and int(exc.code) == 404:
        return True
    return "404" in str(exc)


def user_agent(version: str | None = None) -> str:
    return f"ERGOMS-SECURE-CONNECTION/{version or __version__}"


def parse_version(raw: str) -> tuple[int, ...]:
    text = (raw or "").strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def is_newer(remote: str, local: str) -> bool:
    left = parse_version(remote)
    right = parse_version(local)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def normalize_tag_version(tag: str) -> str:
    text = (tag or "").strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        return text[1:]
    return text


def asset_suffix(platform: str | None = None) -> str:
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        return WIN_ASSET_SUFFIX
    return LINUX_ASSET_SUFFIX


def pick_asset(
    assets: list[Any],
    *,
    platform: str | None = None,
) -> dict[str, str] | None:
    suffix = asset_suffix(platform).lower()
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        hay = f"{name} {url}".lower()
        if suffix in hay and url:
            return {
                "name": name or Path(url).name,
                "url": url,
                "api_url": str(item.get("url") or ""),
            }
    return None


def parse_release(
    payload: dict[str, Any],
    *,
    current: str,
    platform: str | None = None,
) -> ReleaseInfo | None:
    tag = str(payload.get("tag_name") or "")
    version = normalize_tag_version(tag)
    if not version or not is_newer(version, current):
        return None
    picked = pick_asset(list(payload.get("assets") or []), platform=platform)
    if picked is None:
        return None
    return ReleaseInfo(
        version=version,
        tag=tag or f"v{version}",
        html_url=str(payload.get("html_url") or ""),
        asset_name=picked["name"],
        asset_url=picked["url"],
        asset_api_url=picked.get("api_url") or "",
    )


def newest_release_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if payload.get("tag_name"):
            return payload
        return None
    if not isinstance(payload, list):
        return None
    best: dict[str, Any] | None = None
    best_ver = ""
    for item in payload:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        ver = normalize_tag_version(str(item.get("tag_name") or ""))
        if not ver:
            continue
        if best is None or is_newer(ver, best_ver):
            best = item
            best_ver = ver
    return best


def socks_proxy_url(port: int = 1080) -> str | None:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            return f"socks5h://127.0.0.1:{int(port)}"
    except OSError:
        return None


def _curl_bin() -> str | None:
    return shutil.which("curl.exe") or shutil.which("curl")


def http_get(
    url: str,
    *,
    dest: Path | None = None,
    socks_port: int = 1080,
    timeout: int = 60,
    accept: str = _GITHUB_JSON,
) -> bytes:
    """GET *url*. If *dest* is set, write the body there and return b''."""
    proxy = socks_proxy_url(socks_port)
    last_err = ""
    curl = _curl_bin()
    if curl:
        try:
            return _curl_get(
                curl, url, dest=dest, proxy=proxy, timeout=timeout, accept=accept
            )
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            if proxy:
                try:
                    return _curl_get(
                        curl, url, dest=dest, proxy=None, timeout=timeout, accept=accept
                    )
                except Exception as exc2:  # noqa: BLE001
                    last_err = str(exc2)
    try:
        return _urllib_get(url, dest=dest, timeout=timeout, accept=accept)
    except Exception as exc:  # noqa: BLE001
        if last_err:
            raise RuntimeError(f"{exc} (curl: {last_err})") from exc
        raise


def _headers(accept: str) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent(),
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _curl_get(
    curl: str,
    url: str,
    *,
    dest: Path | None,
    proxy: str | None,
    timeout: int,
    accept: str,
) -> bytes:
    args = [
        curl,
        "-fsSL",
        "-A",
        user_agent(),
        "-H",
        f"Accept: {accept}",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        "--connect-timeout",
        "30",
        "--max-time",
        str(max(30, timeout)),
    ]
    token = github_token()
    if token:
        args.extend(["-H", f"Authorization: Bearer {token}"])
    if sys.platform == "win32":
        args.insert(1, "--ssl-no-revoke")
    if proxy:
        args[1:1] = ["--proxy", proxy]
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        args.extend(["-o", str(dest), url])
    else:
        args.append(url)
    result = procutil.run(args, timeout=timeout + 20)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit={result.returncode}"
        raise RuntimeError(err[:300])
    if dest is not None:
        if not dest.is_file() or dest.stat().st_size < 32:
            raise RuntimeError("пустой ответ GitHub")
        return b""
    return (result.stdout or "").encode("utf-8")


def _urllib_get(url: str, *, dest: Path | None, timeout: int, accept: str) -> bytes:
    req = urllib.request.Request(url, headers=_headers(accept))  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        if dest is None:
            return resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            shutil.copyfileobj(resp, out)
    if dest is not None and (not dest.is_file() or dest.stat().st_size < 32):
        raise RuntimeError("пустой ответ GitHub")
    return b""


def _decode_payload(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def _load_release_payload(*, socks_port: int) -> dict[str, Any]:
    repo = github_repo()
    latest = f"https://api.github.com/repos/{repo}/releases/latest"
    listed = f"https://api.github.com/repos/{repo}/releases?per_page=15"
    try:
        payload = _decode_payload(http_get(latest, socks_port=socks_port, timeout=30))
        picked = newest_release_payload(payload)
        if picked is not None:
            return picked
    except Exception as exc:  # noqa: BLE001
        if not is_not_found(exc):
            raise
    payload = _decode_payload(http_get(listed, socks_port=socks_port, timeout=30))
    picked = newest_release_payload(payload)
    if picked is None:
        raise RuntimeError("в GitHub нет опубликованных релизов")
    return picked


def fetch_latest(
    *,
    current: str | None = None,
    socks_port: int = 1080,
    platform: str | None = None,
) -> CheckResult:
    local = current if current is not None else __version__
    try:
        payload = _load_release_payload(socks_port=socks_port)
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:240]
        if is_not_found(exc) and not github_token():
            err = "нет доступа к релизам GitHub (репозиторий закрытый)"
        return CheckResult(error=err)
    message = str(payload.get("message") or "").strip()
    if message and "assets" not in payload:
        return CheckResult(error=message[:240])
    tag = str(payload.get("tag_name") or "")
    version = normalize_tag_version(tag)
    if not version:
        return CheckResult(error="в релизе нет версии")
    if not is_newer(version, local):
        return CheckResult(current_is_latest=True)
    info = parse_release(payload, current=local, platform=platform)
    if info is None:
        return CheckResult(current_is_latest=True)
    return CheckResult(release=info)


def download_asset(
    info: ReleaseInfo,
    dest_dir: Path,
    *,
    socks_port: int = 1080,
) -> Path:
    name = info.asset_name or Path(info.asset_url).name or "update.bin"
    dest = dest_dir / name
    if dest.exists():
        dest.unlink()
    token = github_token()
    if token and info.asset_api_url:
        http_get(
            info.asset_api_url,
            dest=dest,
            socks_port=socks_port,
            timeout=600,
            accept=_GITHUB_ASSET,
        )
    else:
        http_get(info.asset_url, dest=dest, socks_port=socks_port, timeout=600)
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise RuntimeError("скачанный файл слишком маленький")
    return dest


def linux_elev_cmd() -> str | None:
    return shutil.which("pkexec") or shutil.which("sudo")


def extract_linux_archive(archive: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        kwargs: dict[str, Any] = {}
        if sys.version_info >= (3, 12):
            kwargs["filter"] = "data"
        tf.extractall(dest_dir, **kwargs)
    found = sorted(dest_dir.rglob("install.sh"))
    if not found:
        raise RuntimeError("в архиве нет install.sh")
    install_sh = found[0]
    install_sh.chmod(0o755)
    return install_sh.parent


def launch_windows_setup(setup: Path) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        detached = getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= detached
    subprocess.Popen(  # noqa: S603
        [str(setup), *INNO_SILENT_ARGS],
        cwd=str(setup.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def launch_linux_update(extracted_dir: Path, *, pid: int, relaunch: list[str] | None = None) -> None:
    elev = linux_elev_cmd()
    if not elev:
        raise RuntimeError("нужен pkexec или sudo, чтобы поставить обновление")
    install_sh = extracted_dir / "install.sh"
    if not install_sh.is_file():
        raise RuntimeError("не найден install.sh")
    wrapper = extracted_dir / "ergoms-apply-update.sh"
    relaunch_args = relaunch if relaunch is not None else gui_command()
    quoted = " ".join(_sh_quote(a) for a in relaunch_args)
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'pid="{int(pid)}"\n'
        f'install_sh={_sh_quote(str(install_sh))}\n'
        f"elev={_sh_quote(elev)}\n"
        "deadline=$((SECONDS + 180))\n"
        "while kill -0 \"$pid\" 2>/dev/null; do\n"
        "  if (( SECONDS >= deadline )); then\n"
        "    break\n"
        "  fi\n"
        "  sleep 1\n"
        "done\n"
        "sleep 1\n"
        '"$elev" bash "$install_sh"\n'
        f"nohup {quoted} >/dev/null 2>&1 &\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    subprocess.Popen(  # noqa: S603
        ["bash", str(wrapper)],
        cwd=str(extracted_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ.copy(),
    )


def open_release_page(html_url: str) -> None:
    import webbrowser

    target = (html_url or "").strip() or f"https://github.com/{github_repo()}/releases/latest"
    webbrowser.open(target)


def apply_downloaded(archive_or_setup: Path, *, pid: int, relaunch: list[str] | None = None) -> None:
    if sys.platform == "win32":
        launch_windows_setup(archive_or_setup)
        return
    dest = archive_or_setup.parent / "linux-extract"
    if dest.exists():
        shutil.rmtree(dest)
    app_dir = extract_linux_archive(archive_or_setup, dest)
    launch_linux_update(app_dir, pid=pid, relaunch=relaunch)


def can_apply_in_place() -> bool:
    return is_frozen()


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
