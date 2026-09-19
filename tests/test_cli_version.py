from desktop import __version__
from desktop.__main__ import main


def test_version_command(capsys: object) -> None:
    assert main(["version"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert out.strip() == f"ERGOMS SECURE CONNECTION {__version__}"


def test_version_flag(capsys: object) -> None:
    assert main(["--version"]) == 0
    assert main(["-V"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    expected = f"ERGOMS SECURE CONNECTION {__version__}"
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines == [expected, expected]
