from desktop.ui.clipboard import copy_text


def test_copy_text_falls_back_to_win32(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.ui.clipboard.sys.platform", "win32")
    monkeypatch.setattr("desktop.ui.clipboard._copy_via_qt", lambda *_a, **_k: False)
    monkeypatch.setattr("desktop.ui.clipboard._copy_via_win32", lambda *_a, **_k: True)
    assert copy_text("hello") is True


def test_copy_text_false_when_both_fail(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.ui.clipboard.sys.platform", "linux")
    monkeypatch.setattr("desktop.ui.clipboard._copy_via_qt", lambda *_a, **_k: False)
    assert copy_text("hello") is False
