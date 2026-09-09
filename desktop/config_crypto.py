"""Password-based encrypt/decrypt for config.json transfer (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
from pathlib import Path
from typing import Any

MAGIC = b"OPSENC1\n"
SALT_LEN = 16
NONCE_LEN = 16
MAC_LEN = 32
PBKDF2_ITERS = 390_000


def _derive(password: str, salt: bytes) -> tuple[bytes, bytes]:
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERS,
        dklen=64,
    )
    return raw[:32], raw[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            key, nonce + struct.pack(">Q", counter), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_bytes(plaintext: bytes, password: str) -> bytes:
    if not password:
        raise ValueError("Password is required")
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    enc_key, mac_key = _derive(password, salt)
    stream = _keystream(enc_key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    mac = hmac.new(
        mac_key, salt + nonce + ciphertext, hashlib.sha256
    ).digest()
    return MAGIC + salt + nonce + ciphertext + mac


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    if not password:
        raise ValueError("Password is required")
    if not blob.startswith(MAGIC):
        raise ValueError("Not an ERGOMS VPN encrypted config (bad magic)")
    body = blob[len(MAGIC) :]
    if len(body) < SALT_LEN + NONCE_LEN + MAC_LEN:
        raise ValueError("Encrypted file is truncated")
    salt = body[:SALT_LEN]
    nonce = body[SALT_LEN : SALT_LEN + NONCE_LEN]
    mac = body[-MAC_LEN:]
    ciphertext = body[SALT_LEN + NONCE_LEN : -MAC_LEN]
    enc_key, mac_key = _derive(password, salt)
    expected = hmac.new(
        mac_key, salt + nonce + ciphertext, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("Wrong password or corrupted file")
    stream = _keystream(enc_key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream))


def encrypt_config(cfg: dict[str, Any], password: str) -> bytes:
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    return encrypt_bytes(text.encode("utf-8"), password)


def decrypt_config(blob: bytes, password: str) -> dict[str, Any]:
    raw = decrypt_bytes(blob, password)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Decrypted payload is not a JSON object")
    return data


def encrypt_file(src: Path, dst: Path, password: str) -> None:
    plaintext = src.read_bytes()
    # Normalize: re-serialize if valid JSON for stable transfer
    try:
        cfg = json.loads(plaintext.decode("utf-8-sig"))
        if isinstance(cfg, dict):
            blob = encrypt_config(cfg, password)
        else:
            blob = encrypt_bytes(plaintext, password)
    except (UnicodeDecodeError, json.JSONDecodeError):
        blob = encrypt_bytes(plaintext, password)
    dst.write_bytes(blob)


def decrypt_file(src: Path, dst: Path, password: str) -> dict[str, Any]:
    cfg = decrypt_config(src.read_bytes(), password)
    dst.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return cfg
