from __future__ import annotations

from pathlib import Path

from desktop.ssh_setup import (
    INCLUDE_NAME,
    _strip_managed_hosts,
    client_ssh_block,
    copy_id_targets,
    default_hosts,
    ensure_user_include,
    posix_path,
    render_include,
)


def test_render_include_uses_wrapper_and_jump(tmp_path: Path) -> None:
    cfg = {
        "server": {"host": "203.0.113.10", "port": 443, "local_socks_port": 1080},
        "reverse_ssh": {"vps_user": "root", "vps_port": 22},
        "client_ssh": {"hosts": default_hosts({})},
    }
    identity = tmp_path / "server-vps"
    wrapper = tmp_path / "ergoms-connect-socks.cmd"
    text = render_include(cfg=cfg, identity=identity, wrapper=wrapper)
    assert "Host vps-server server-vps" in text
    assert "Host vps-server-direct" in text
    assert "Host bstu-server-laboratory-proxy-1 bstu-server-laboratory-1 lab" in text
    assert f"ProxyCommand {posix_path(wrapper)} %h %p" in text
    assert "ProxyJump vps-server" in text
    assert "HostName 203.0.113.10" in text
    assert "HostName 127.0.0.1" in text
    assert "Port 2222" in text
    assert "User dohao" in text
    assert f"IdentityFile {posix_path(identity)}" in text


def test_copy_id_targets_are_lab_aliases() -> None:
    cfg = {"client_ssh": {"hosts": default_hosts({})}}
    assert copy_id_targets(cfg) == ["bstu-server-laboratory-proxy-1"]


def test_ensure_user_include_keeps_foreign_hosts(tmp_path: Path, monkeypatch) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text(
        "\n".join(
            [
                "Host vps-server",
                "  HostName old.example",
                "",
                "Host bstu-adm",
                "  HostName 10.17.0.110",
                "  User administrator",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("desktop.ssh_setup.Path.home", lambda: tmp_path)
    ensure_user_include(ssh_dir)
    text = (ssh_dir / "config").read_text(encoding="utf-8")
    assert f"Include {INCLUDE_NAME}" in text
    assert "Host bstu-adm" in text
    assert "Host vps-server" not in text


def test_strip_managed_keeps_unrelated() -> None:
    raw = "Host bstu-adm\n  User administrator\n\nHost lab\n  User dohao\n"
    assert "bstu-adm" in _strip_managed_hosts(raw)
    assert "Host lab" not in _strip_managed_hosts(raw)


def test_client_ssh_defaults_when_missing() -> None:
    block = client_ssh_block({})
    assert block["hosts"]
    assert any("vps-server" in str(h.get("host")) for h in block["hosts"])
