from desktop.procutil import pid_alive


def test_pid_alive_eperm_means_process_exists(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.procutil.sys.platform", "linux")
    monkeypatch.setattr(
        "desktop.procutil.os.kill",
        lambda _pid, _sig: (_ for _ in ()).throw(PermissionError("EPERM")),
    )
    monkeypatch.setattr("desktop.procutil._linux_proc_state", lambda _pid: "S")
    assert pid_alive(502625) is True


def test_pid_alive_missing_pid_is_dead(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.procutil.sys.platform", "linux")
    monkeypatch.setattr(
        "desktop.procutil.os.kill",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError("ESRCH")),
    )
    assert pid_alive(502625) is False


def test_pid_alive_zombie_is_dead(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.procutil.sys.platform", "linux")
    monkeypatch.setattr("desktop.procutil.os.kill", lambda _pid, _sig: None)
    monkeypatch.setattr("desktop.procutil._linux_proc_state", lambda _pid: "Z")
    assert pid_alive(502625) is False
