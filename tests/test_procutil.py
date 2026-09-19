from desktop.procutil import listen_ports_open, pid_alive


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


class _CmdOut:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def test_listen_ports_open_when_ss_hides_root_pid(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.procutil.sys.platform", "linux")

    def fake_run(argv: list[str], **_kw: object) -> _CmdOut:
        filt = " ".join(argv)
        header = "State Recv-Q Send-Q Local Address:Port Peer Address:PortProcess\n"
        if "-ltnp" in argv:
            return _CmdOut(header)
        if "sport = :1080" in filt:
            return _CmdOut(header + "LISTEN 0 4096 127.0.0.1:1080 0.0.0.0:*\n")
        return _CmdOut(header)

    monkeypatch.setattr("desktop.procutil.run", fake_run)
    got = listen_ports_open([1080, 1088], cache=False)
    assert got[1080] is True
    assert got[1088] is False

