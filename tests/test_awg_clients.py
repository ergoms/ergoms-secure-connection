"""AmneziaWG multi-client helpers (no live VPS)."""

from __future__ import annotations

import json
from pathlib import Path

from modes.vps.awg_clients import (
    load_peers,
    next_address,
    next_name,
    render_server_conf,
    sanitize_name,
    save_peers,
    used_host_ids,
)


def test_next_name_and_address() -> None:
    assert next_name(set()) == "pc"
    assert next_name({"pc"}) == "pc2"
    assert next_name({"pc", "pc2"}) == "pc3"
    peers = [{"address": "10.66.66.2/32"}]
    assert next_address(peers) == "10.66.66.3/32"
    assert 1 in used_host_ids(peers)
    assert 2 in used_host_ids(peers)
    assert sanitize_name("phone_1") == "phone_1"


def test_sanitize_name_rejects_spaces() -> None:
    import pytest

    with pytest.raises(ValueError):
        sanitize_name("my phone")


def test_peers_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "peers.json"
    save_peers(
        [
            {
                "name": "pc",
                "private_key": "a",
                "public_key": "b",
                "psk": "c",
                "address": "10.66.66.2/32",
            }
        ],
        path=path,
    )
    got = load_peers(path)
    assert got[0]["name"] == "pc"
    assert next_address(got) == "10.66.66.3/32"


def test_server_conf_lists_all_peers() -> None:
    creds = {
        "AWG_PORT": "51820",
        "AWG_SERVER_PRIVATE": "server-priv",
        "AWG_ADDRESS_SERVER": "10.66.66.1/24",
        "AWG_JC": "4",
        "AWG_JMIN": "40",
        "AWG_JMAX": "70",
        "AWG_S1": "15",
        "AWG_S2": "20",
        "AWG_H1": "1",
        "AWG_H2": "2",
        "AWG_H3": "3",
        "AWG_H4": "4",
    }
    peers = [
        {"name": "pc", "public_key": "pub-a", "psk": "psk-a", "address": "10.66.66.2/32"},
        {"name": "phone", "public_key": "pub-b", "psk": "psk-b", "address": "10.66.66.3/32"},
    ]
    text = render_server_conf(creds, peers, wan_iface="eth0")
    assert text.count("[Peer]") == 2
    assert "pub-a" in text and "pub-b" in text
    assert "10.66.66.2/32" in text and "10.66.66.3/32" in text
    assert "ListenPort = 51820" in text
    json.dumps(peers)
