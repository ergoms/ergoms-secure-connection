from desktop.proc_net import _friendly_service_display


_SENSE_MUI = (
    r"@%ProgramFiles%\Windows Defender Advanced Threat Protection\MsSense.exe,-1001"
)


def test_friendly_display_keeps_plain_name() -> None:
    assert _friendly_service_display("Print Spooler", "Spooler") == "Print Spooler"


def test_friendly_display_does_not_leak_mui_resource() -> None:
    got = _friendly_service_display(_SENSE_MUI, "Sense")
    assert got
    assert not got.startswith("@")
    assert ",-1001" not in got


def test_friendly_display_empty_falls_back() -> None:
    assert _friendly_service_display("", "Sense") == "Sense"
