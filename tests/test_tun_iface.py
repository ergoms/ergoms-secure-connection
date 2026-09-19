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
