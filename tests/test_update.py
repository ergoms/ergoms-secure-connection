"""GitHub release check helpers (no live network)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from desktop.update import (
    DEFAULT_GITHUB_REPO,
    CheckResult,
    apply_downloaded,
    asset_suffix,
    extract_linux_archive,
    fetch_latest,
    github_repo,
    is_newer,
    newest_release_payload,
    parse_release,
    parse_version,
    pick_asset,
)


def _payload(tag: str = "v1.2.7") -> dict:
    ver = tag[1:] if tag.startswith("v") else tag
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/ergoms/ergoms-secure-connection/releases/tag/{tag}",
        "assets": [
            {
                "name": f"ERGOMS-SECURE-CONNECTION-{ver}-windows-x64-setup.exe",
                "browser_download_url": (
                    "https://github.com/ergoms/ergoms-secure-connection/releases/"
                    f"download/{tag}/ERGOMS-SECURE-CONNECTION-{ver}-windows-x64-setup.exe"
                ),
            },
            {
                "name": f"ERGOMS-SECURE-CONNECTION-{ver}-linux-x64.tar.gz",
                "browser_download_url": (
                    "https://github.com/ergoms/ergoms-secure-connection/releases/"
                    f"download/{tag}/ERGOMS-SECURE-CONNECTION-{ver}-linux-x64.tar.gz"
                ),
            },
            {
                "name": f"ERGOMS-SECURE-CONNECTION-{ver}-windows-x64.zip",
                "browser_download_url": (
                    "https://github.com/ergoms/ergoms-secure-connection/releases/"
                    f"download/{tag}/ERGOMS-SECURE-CONNECTION-{ver}-windows-x64.zip"
                ),
            },
        ],
    }


def test_parse_version_strips_v_prefix() -> None:
    assert parse_version("v1.2.7") == (1, 2, 7)
    assert parse_version("1.2.6") == (1, 2, 6)
    assert parse_version("") == (0,)


def test_is_newer_semver() -> None:
    assert is_newer("1.2.7", "1.2.6")
    assert is_newer("v1.10.0", "1.9.9")
    assert not is_newer("1.2.6", "1.2.6")
    assert not is_newer("1.2.5", "1.2.6")
    assert not is_newer("v1.2.6", "1.2.6")


def test_pick_asset_windows_prefers_setup() -> None:
    picked = pick_asset(_payload()["assets"], platform="win32")
    assert picked is not None
    assert picked["name"].endswith("windows-x64-setup.exe")
    assert "windows-x64-setup.exe" in picked["url"]


def test_pick_asset_linux_tarball() -> None:
    picked = pick_asset(_payload()["assets"], platform="linux")
    assert picked is not None
    assert picked["name"].endswith("linux-x64.tar.gz")


def test_asset_suffix() -> None:
    assert asset_suffix("win32") == "windows-x64-setup.exe"
    assert asset_suffix("linux") == "linux-x64.tar.gz"


def test_parse_release_newer() -> None:
    info = parse_release(_payload("v1.2.7"), current="1.2.6", platform="win32")
    assert info is not None
    assert info.version == "1.2.7"
    assert info.asset_name.endswith("setup.exe")
    assert info.html_url.endswith("/v1.2.7")


def test_parse_release_same_version_is_none() -> None:
    assert parse_release(_payload("v1.2.6"), current="1.2.6", platform="win32") is None


def test_parse_release_missing_platform_asset() -> None:
    payload = _payload("v9.9.9")
    payload["assets"] = [
        {
            "name": "notes.txt",
            "browser_download_url": "https://example.invalid/notes.txt",
        }
    ]
    assert parse_release(payload, current="1.0.0", platform="win32") is None


def test_fetch_latest_newer(monkeypatch: object) -> None:
    raw = json.dumps(_payload("v1.2.7")).encode("utf-8")

    def _fake_get(url: str, **kwargs: object) -> bytes:
        assert "releases/latest" in url
        return raw

    monkeypatch.setattr("desktop.update.http_get", _fake_get)
    result = fetch_latest(current="1.2.6", platform="win32")
    assert result.error == ""
    assert result.release is not None
    assert result.release.version == "1.2.7"


def test_fetch_latest_already_current(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "desktop.update.http_get",
        lambda url, **kwargs: json.dumps(_payload("v1.2.6")).encode("utf-8"),
    )
    result = fetch_latest(current="1.2.6", platform="win32")
    assert result.current_is_latest
    assert result.release is None


def test_github_repo_default() -> None:
    assert github_repo() == DEFAULT_GITHUB_REPO


def test_newest_release_skips_draft_and_prerelease() -> None:
    draft = _payload("v9.9.9")
    draft["draft"] = True
    pre = _payload("v8.8.8")
    pre["prerelease"] = True
    older = _payload("v1.2.0")
    newer = _payload("v1.2.8")
    picked = newest_release_payload([draft, pre, older, newer])
    assert picked is not None
    assert picked["tag_name"] == "v1.2.8"


def test_fetch_latest_falls_back_to_releases_list(monkeypatch: object) -> None:
    def _fake_get(url: str, **kwargs: object) -> bytes:
        if "/latest" in url:
            raise RuntimeError("HTTP Error 404: Not Found (curl: curl: (22) The requested URL returned error: 404)")
        return json.dumps([_payload("v1.2.7")]).encode("utf-8")

    monkeypatch.setattr("desktop.update.http_get", _fake_get)
    result = fetch_latest(current="1.2.6", platform="win32")
    assert result.error == ""
    assert result.release is not None
    assert result.release.version == "1.2.7"


def test_fetch_latest_network_error(monkeypatch: object) -> None:
    def _boom(url: str, **kwargs: object) -> bytes:
        raise RuntimeError("HTTP Error 404: Not Found")

    monkeypatch.setattr("desktop.update.http_get", _boom)
    result = fetch_latest(current="1.2.6")
    assert result.release is None
    assert result.error



def test_check_result_as_dict() -> None:
    empty = CheckResult(current_is_latest=True)
    assert empty.as_dict()["release"] is None
    assert empty.as_dict()["current_is_latest"] is True


def test_extract_linux_archive(tmp_path: Path) -> None:
    src = tmp_path / "src" / "ERGOMS SECURE CONNECTION"
    src.mkdir(parents=True)
    (src / "install.sh").write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
    (src / "ERGOMS SECURE CONNECTION").write_bytes(b"bin")
    archive = tmp_path / "app.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname="ERGOMS SECURE CONNECTION")
    extracted = extract_linux_archive(archive, tmp_path / "out")
    assert (extracted / "install.sh").is_file()


def test_apply_downloaded_windows_setup(tmp_path: Path, monkeypatch: object) -> None:
    setup = tmp_path / "ERGOMS-SECURE-CONNECTION-1.2.7-windows-x64-setup.exe"
    setup.write_bytes(b"mz" * 600)
    called: list[list[str]] = []

    class _Proc:
        def __init__(self, args: list[str], **kwargs: object) -> None:
            called.append(list(args))

    monkeypatch.setattr("desktop.update.sys.platform", "win32")
    monkeypatch.setattr("desktop.update.subprocess.Popen", _Proc)
    monkeypatch.setattr("desktop.update.subprocess.CREATE_NEW_PROCESS_GROUP", 0, raising=False)
    monkeypatch.setattr("desktop.update.subprocess.DETACHED_PROCESS", 0, raising=False)
    apply_downloaded(setup, pid=1)
    assert called
    assert called[0][0] == str(setup)
    assert "/SILENT" in called[0]
    assert "/MERGETASKS=removeold,!wipeconfigs" in called[0]
