from __future__ import annotations

from pathlib import Path

from desktop.ssh_setup import (
    _strip_managed_hosts,
    client_ssh_block,
    copy_id_targets,
    default_hosts,
    posix_path,
    render_include,
    ssh_identity_line,
    write_user_config,
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
    assert "Host vps-server-direct" in text
    assert "Host vps-server\n" in text
    assert "Host bstu-server-laboratory-proxy-1" in text
    assert "Host server-vps" not in text
    assert "Host lab" not in text
    assert "bstu-server-laboratory-1" not in text
    assert f"ProxyCommand {posix_path(wrapper)} %h %p" in text
    assert "ProxyJump vps-server" in text
    assert "HostName 203.0.113.10" in text
    assert "HostName 127.0.0.1" in text
    assert "Port 2222" in text
    assert "User dohao" in text
    assert f"IdentityFile {ssh_identity_line(identity)}" in text
    assert "PreferredAuthentications" not in text


def test_copy_id_targets_are_lab_aliases() -> None:
    cfg = {"client_ssh": {"hosts": default_hosts({})}}
    assert copy_id_targets(cfg) == ["bstu-server-laboratory-proxy-1"]


def test_write_user_config_keeps_foreign_hosts(tmp_path: Path) -> None:
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
    write_user_config(ssh_dir, "Host vps-server server-vps\n  HostName 203.0.113.10\n")
    text = (ssh_dir / "config").read_text(encoding="utf-8")
    assert "Host bstu-adm" in text
    assert "Include " not in text
    assert "HostName 203.0.113.10" in text
    assert "old.example" not in text


def test_strip_managed_keeps_unrelated() -> None:
    raw = "Host bstu-adm\n  User administrator\n\nHost lab\n  User dohao\n"
    assert "bstu-adm" in _strip_managed_hosts(raw)
    assert "Host lab" not in _strip_managed_hosts(raw)


def test_client_ssh_defaults_when_missing() -> None:
    block = client_ssh_block({})
    assert block["hosts"]
    assert any("vps-server" in str(h.get("host")) for h in block["hosts"])
