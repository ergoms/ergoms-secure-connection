"""TUN device name: Linux IFNAMSIZ is 15 characters."""

from desktop.net._impl import LINUX_TUN_IFACE_NAME, WIN_TUN_IFACE_NAME, tun_iface_name
from desktop.singbox.manager import _tun_inbound


def test_linux_tun_name_fits_ifnamsiz() -> None:
    assert len(LINUX_TUN_IFACE_NAME) <= 15
    assert tun_iface_name(platform="linux") == "ergoms-tun"
    assert tun_iface_name(platform="win32") == WIN_TUN_IFACE_NAME
    assert " " not in LINUX_TUN_IFACE_NAME


def test_tun_inbound_uses_short_linux_name(monkeypatch: object) -> None:
    monkeypatch.setattr("desktop.singbox.manager.tun_iface_name", lambda: "ergoms-tun")
    inbound = _tun_inbound(1400, kill_switch=True, route_exclude=[])
    assert inbound["interface_name"] == "ergoms-tun"
    assert len(inbound["interface_name"]) <= 15


def test_linux_split_route_lines() -> None:
    from desktop.net._impl import _linux_split_to_row, tun_owns_default

    lo = _linux_split_to_row("0.0.0.0/1 dev ergoms-tun proto static scope link")
    hi = _linux_split_to_row("128.0.0.0/1 dev ergoms-tun proto static scope link")
    assert lo is not None and "ergoms-tun" in lo
    assert hi is not None and "ergoms-tun" in hi
    assert tun_owns_default([lo, hi]) is True
    assert _linux_split_to_row("default via 10.17.0.1 dev enp4s0") is None


def test_linux_install_tun_split_uses_ip_route(monkeypatch: object) -> None:
    from desktop.tun import install_tun_split_default

    monkeypatch.setattr("desktop.net._impl.sys.platform", "linux")
    cmds = install_tun_split_default(1)
    joined = "\n".join(cmds)
    assert "ip route replace 0.0.0.0/1 dev ergoms-tun" in joined
    assert "ip route replace 128.0.0.0/1 dev ergoms-tun" in joined
    assert "route add" not in joined
