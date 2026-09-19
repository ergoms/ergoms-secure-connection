"""Linux sudo handoff for TUN (config stays in the user's home)."""

from __future__ import annotations

from desktop.procutil import linux_root_env, relaunch_as_admin


def test_linux_data_dir_uses_sudo_user_not_root(monkeypatch: object) -> None:
    import sys
    import types

    from desktop.paths import linux_data_dir

    monkeypatch.setenv("SUDO_USER", "administrator")
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("XDG_DATA_HOME", "/root/.local/share")
    monkeypatch.setattr("desktop.paths.os.geteuid", lambda: 0, raising=False)
    fake_pwd = types.SimpleNamespace(
        getpwnam=lambda _name: types.SimpleNamespace(pw_dir="/home/administrator")
    )
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    got = linux_data_dir()
    assert got.as_posix().endswith("/home/administrator/.local/share/ergoms-secure-connection")


def test_linux_data_dir_xdg_without_sudo(monkeypatch: object) -> None:
    from desktop.paths import linux_data_dir

    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
    monkeypatch.setattr("desktop.paths.os.geteuid", lambda: 1000, raising=False)
    got = linux_data_dir()
    assert got.as_posix().endswith("/custom/xdg/ergoms-secure-connection")


def test_linux_root_env_keeps_home_and_data(monkeypatch: object) -> None:
    monkeypatch.setenv("HOME", "/home/administrator")
    monkeypatch.setenv("USER", "administrator")
    monkeypatch.setenv("ERGOMS_SC_DATA", "/home/administrator/.local/share/ergoms-secure-connection")
    monkeypatch.setenv("DISPLAY", ":0")
    pairs = linux_root_env()
    assert "HOME=/home/administrator" in pairs
    assert "USER=administrator" in pairs
    assert (
        "ERGOMS_SC_DATA=/home/administrator/.local/share/ergoms-secure-connection"
        in pairs
    )
    assert "DISPLAY=:0" in pairs


def test_relaunch_as_admin_linux_execs_sudo(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.procutil.sys.platform", "linux")
    monkeypatch.setattr("desktop.procutil.is_admin", lambda: False)
    monkeypatch.setattr(
        "desktop.procutil.shutil.which",
        lambda name: "/usr/bin/sudo" if name == "sudo" else None,
    )
    monkeypatch.setattr("desktop.procutil.sys.stdin.isatty", lambda: True)
    monkeypatch.setenv("HOME", "/home/administrator")
    monkeypatch.setenv("USER", "administrator")
    monkeypatch.setenv("ERGOMS_SC_DATA", "/home/administrator/.local/share/esc")
    called: list[list[str]] = []

    def _execvp(file: str, argv: list[str]) -> None:
        called.append(list(argv))
        raise OSError("stop")

    monkeypatch.setattr("desktop.procutil.os.execvp", _execvp)
    assert relaunch_as_admin(["/usr/local/bin/ergoms-sc", "on"]) is False
    assert called
    argv = called[0]
    assert argv[0] == "/usr/bin/sudo"
    assert argv[1] == "env"
    assert "HOME=/home/administrator" in argv
    assert "ERGOMS_SC_DATA=/home/administrator/.local/share/esc" in argv
    assert argv[-2:] == ["/usr/local/bin/ergoms-sc", "on"]


def test_relaunch_as_admin_skips_when_already_root(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.procutil.is_admin", lambda: True)
    monkeypatch.setattr(
        "desktop.procutil.os.execvp",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not exec")),
    )
    assert relaunch_as_admin(["ergoms-sc", "on"]) is False
