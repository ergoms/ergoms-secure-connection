from __future__ import annotations

from pathlib import Path

from desktop.rustdesk_opt import (
    restore_toml_options,
    rustdesk_tun_lan_reject_rules,
    set_toml_options,
)
from desktop.tun import RUSTDESK_PROCS, TUN_LAN_CIDR


def test_tun_lan_reject_targets_rustdesk_only() -> None:
    rules = rustdesk_tun_lan_reject_rules()
    assert rules
    rule = rules[0]
    assert rule["action"] == "reject"
    assert TUN_LAN_CIDR in rule["ip_cidr"]
    assert "rustdesk.exe" in [n.lower() for n in rule["process_name"]]
    assert set(n.lower() for n in rule["process_name"]) <= {n.lower() for n in RUSTDESK_PROCS}


def test_set_and_restore_toml_options(tmp_path: Path) -> None:
    path = tmp_path / "RustDesk2.toml"
    path.write_text(
        "rendezvous_server = '193.23.202.147:21116'\n"
        "\n"
        "[options]\n"
        "relay-server = '193.23.202.147'\n"
        "av1-test = 'N'\n",
        encoding="utf-8",
    )
    prev = set_toml_options(
        path, {"allow-always-relay": "Y", "force-always-relay": "Y"}
    )
    assert prev["allow-always-relay"] is None
    text = path.read_text(encoding="utf-8")
    assert "force-always-relay = 'Y'" in text
    assert "relay-server = '193.23.202.147'" in text
    restore_toml_options(path, prev)
    restored = path.read_text(encoding="utf-8")
    assert "force-always-relay" not in restored
    assert "relay-server = '193.23.202.147'" in restored


def test_restore_keeps_previous_value(tmp_path: Path) -> None:
    path = tmp_path / "RustDesk2.toml"
    path.write_text("[options]\nallow-always-relay = 'N'\n", encoding="utf-8")
    prev = set_toml_options(path, {"allow-always-relay": "Y"})
    assert prev["allow-always-relay"] == "N"
    restore_toml_options(path, prev)
    assert "allow-always-relay = 'N'" in path.read_text(encoding="utf-8")
