"""Windows route/netsh decoding — OEM 'по умолчанию' must not become CJK."""

from __future__ import annotations

from pathlib import Path

import pytest

from desktop.sys.win_net import (
    NetshIface,
    decode_win_console,
    normalize_win_net_text,
)


def test_decode_cp866_default_metric_is_russian_not_cjk() -> None:
    raw = "0.0.0.0          0.0.0.0      10.193.0.1      по умолчанию\r\n".encode(
        "cp866"
    )
    text = decode_win_console(raw)
    assert "умолчан" in text
    assert "㬮" not in text
    assert "\ufffd" not in text


def test_utf8_misread_of_cp866_looks_like_cjk() -> None:
    raw = "по умолчанию".encode("cp866")
    broken = raw.decode("utf-8", errors="replace")
    assert "㬮" in broken or "\ufffd" in broken
    assert decode_win_console(raw) == "по умолчанию"


def test_normalize_russian_default_metric() -> None:
    line = "0.0.0.0 0.0.0.0 10.193.0.1 по умолчанию"
    assert normalize_win_net_text(line) == "0.0.0.0 0.0.0.0 10.193.0.1 Default"


def test_decode_prefers_utf8_when_already_unicode() -> None:
    raw = "0.0.0.0 0.0.0.0 10.193.0.1 по умолчанию\n".encode("utf-8")
    text = decode_win_console(raw)
    assert "умолчан" in text
    assert "㬮" not in text


def test_netsh_iface_up_accepts_russian_connected() -> None:
    assert NetshIface(10, 25, 1500, "connected", "Ethernet").up
    assert NetshIface(10, 25, 1500, "Подключен", "Ethernet").up
    assert NetshIface(10, 25, 1500, "подключено", "Ethernet").up
    assert not NetshIface(
        28, 5, 1280, "disconnected", "ergoms-secure-connection-tun"
    ).up


def test_win_if_index_finds_tun_before_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop.tun import TUN_IFACE_NAME, _win_if_index_by_alias

    monkeypatch.setattr(
        "desktop.tun.netsh_ipv4_interfaces",
        lambda: [
            NetshIface(10, 25, 1500, "connected", "Ethernet"),
            NetshIface(28, 5, 1280, "disconnected", TUN_IFACE_NAME),
        ],
    )
    assert _win_if_index_by_alias(TUN_IFACE_NAME) == 28
    assert _win_if_index_by_alias(TUN_IFACE_NAME, require_up=True) is None
    assert _win_if_index_by_alias("Ethernet") == 10


def test_wait_tun_iface_ignores_disconnected_leftover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop.tun import TUN_IFACE_NAME, wait_tun_iface

    monkeypatch.setattr("desktop.tun.sys.platform", "win32")
    monkeypatch.setattr(
        "desktop.tun.netsh_ipv4_interfaces",
        lambda: [NetshIface(28, 5, 1280, "disconnected", TUN_IFACE_NAME)],
    )
    assert wait_tun_iface(timeout=0.01) is None


def test_default_route_lines_use_default_not_mojibake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from desktop.tun import default_route_lines

    text = (
        "0.0.0.0          0.0.0.0      10.193.0.1    10.193.0.102     281\n"
        "0.0.0.0          0.0.0.0      10.193.0.1      Default\n"
        "127.0.0.0        255.0.0.0    On-link         127.0.0.1     331\n"
    )
    monkeypatch.setattr("desktop.tun.route_print_v4", lambda: text)
    monkeypatch.setattr("desktop.tun.sys.platform", "win32")
    rows = default_route_lines()
    assert rows[0] == "0.0.0.0 0.0.0.0 10.193.0.1 10.193.0.102 281"
    assert rows[1] == "0.0.0.0 0.0.0.0 10.193.0.1 Default"
    assert all("㬮" not in row for row in rows)


def test_wait_ready_needs_tun_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from desktop.singbox_mode import SingboxModeManager

    logs: list[str] = []
    mgr = SingboxModeManager(tmp_path, tmp_path, tmp_path, log=logs.append)
    monkeypatch.setattr("desktop.singbox_mode.sys.platform", "win32")
    monkeypatch.setattr("desktop.singbox_mode.port_open", lambda *_a, **_k: True)
    monkeypatch.setattr("desktop.singbox_mode.procutil.pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        "desktop.singbox_mode.wait_tun_iface", lambda timeout=0.05: None
    )
    monkeypatch.setattr(mgr, "tail_log", lambda n=40: [])
    assert mgr._tun_inbound_ready() is False
    assert mgr._wait_ready(1080, 1088, enable_tun=True, pid=1, timeout=0.15) is False
    assert any("TUN ещё не поднялся" in msg for msg in logs)

    monkeypatch.setattr(
        "desktop.singbox_mode.wait_tun_iface", lambda timeout=0.05: 28
    )
    assert mgr._tun_inbound_ready() is True
    assert mgr._wait_ready(1080, 1088, enable_tun=True, pid=1, timeout=0.15) is True


def test_tun_still_opening_from_wintun_warning(tmp_path: Path) -> None:
    from desktop.singbox_mode import SingboxModeManager

    mgr = SingboxModeManager(tmp_path, tmp_path, tmp_path, log=lambda _m: None)
    mgr.tail_log = lambda n=40: [  # type: ignore[method-assign]
        "WARN inbound/tun[tun-in]: open interface take too much time to finish!"
    ]
    assert mgr._tun_still_opening() is True
    assert mgr._tun_adapter_busy() is True
    assert mgr._tun_inbound_ready() is False
    mgr.tail_log = lambda n=40: [  # type: ignore[method-assign]
        "INFO inbound/tun[tun-in]: started at ergoms-secure-connection-tun"
    ]
    assert mgr._tun_still_opening() is False
    assert mgr._tun_inbound_ready() is True


def test_tun_inbound_ready_ignores_singbox_started_while_wintun_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from desktop.singbox_mode import SingboxModeManager

    mgr = SingboxModeManager(tmp_path, tmp_path, tmp_path, log=lambda _m: None)
    mgr.tail_log = lambda n=40: [  # type: ignore[method-assign]
        "WARN inbound/tun[tun-in]: open interface take too much time to finish!",
        "INFO sing-box started (0.00s)",
    ]
    monkeypatch.setattr(
        "desktop.singbox_mode.wait_tun_iface", lambda timeout=0.05: 28
    )
    assert mgr._tun_log_started() is False
    assert mgr._tun_still_opening() is True
    assert mgr._tun_inbound_ready() is False
