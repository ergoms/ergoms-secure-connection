"""systemd unit quoting for the Linux client (spaces in the binary name)."""

from __future__ import annotations

from pathlib import Path

from desktop.linux_service import render_unit, service_bin, systemd_exec, systemd_quote


def test_systemd_quote_wraps_spaces() -> None:
    assert systemd_quote("/opt/app/ERGOMS SECURE CONNECTION") == (
        '"/opt/app/ERGOMS SECURE CONNECTION"'
    )


def test_systemd_exec_quotes_binary_not_args() -> None:
    line = systemd_exec("/opt/app/ERGOMS SECURE CONNECTION", "watch")
    assert line == '"/opt/app/ERGOMS SECURE CONNECTION" watch'
    assert "\\" not in line


def test_render_unit_quotes_exec_start(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.linux_service.service_bin", lambda exe: Path(exe))
    exe = tmp_path / "ERGOMS SECURE CONNECTION"
    text = render_unit(
        exe=exe,
        data=Path("/home/administrator/.local/share/ergoms-secure-connection"),
        home="/home/administrator",
        user="administrator",
    )
    assert 'ExecStart="/' in text or "ExecStart=" in text
    assert "ERGOMS\\ SECURE" not in text
    assert '" watch' in text or " watch\n" in text
    start = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    assert start.startswith('ExecStart="')
    assert start.endswith('" watch')
    stop = next(line for line in text.splitlines() if line.startswith("ExecStop="))
    assert stop.endswith('" off')
    # WorkingDirectory= is a literal path: quotes become part of the value
    # and systemd then says "path is not absolute".
    wd = next(line for line in text.splitlines() if line.startswith("WorkingDirectory="))
    assert wd == f"WorkingDirectory={exe.parent}"
    assert not wd.startswith('WorkingDirectory="')


def test_service_bin_falls_back_when_no_symlink(tmp_path: Path) -> None:
    exe = tmp_path / "ERGOMS SECURE CONNECTION"
    exe.write_bytes(b"x")
    exe.chmod(0o755)
    assert service_bin(exe) == exe
