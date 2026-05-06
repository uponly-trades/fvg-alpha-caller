import base64
import os

import pytest

from trade_executor.crypto import encrypt, decrypt


@pytest.fixture
def key() -> bytes:
    return os.urandom(32)


def test_round_trip_string(key):
    plaintext = "binance_api_key_AbCd1234"
    blob = encrypt(plaintext, key)
    assert decrypt(blob, key) == plaintext


def test_blob_format_nonce_ct_tag(key):
    blob = encrypt("hello", key)
    assert len(blob) >= 28
    assert isinstance(blob, bytes)


def test_two_encrypts_have_different_nonces(key):
    a = encrypt("same", key)
    b = encrypt("same", key)
    assert a != b


def test_wrong_key_fails(key):
    blob = encrypt("secret", key)
    with pytest.raises(Exception):
        decrypt(blob, os.urandom(32))


def test_tampered_ciphertext_fails(key):
    blob = bytearray(encrypt("secret", key))
    blob[20] ^= 0x01
    with pytest.raises(Exception):
        decrypt(bytes(blob), key)


def test_b64_master_key_roundtrip():
    raw = os.urandom(32)
    b64 = base64.b64encode(raw).decode()
    decoded = base64.b64decode(b64)
    assert decoded == raw
    blob = encrypt("x", decoded)
    assert decrypt(blob, decoded) == "x"
