"""AES-256-GCM with format: nonce(12) || ciphertext || tag(16).

Master key (32 bytes) loaded from base64 env var by caller. Module is pure.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_LEN = 12


def encrypt(plaintext: str, key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes (AES-256)")
    nonce = os.urandom(NONCE_LEN)
    ct_with_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct_with_tag


def decrypt(blob: bytes, key: bytes) -> str:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes (AES-256)")
    if len(blob) < NONCE_LEN + 16:
        raise ValueError("blob too short")
    nonce = blob[:NONCE_LEN]
    ct_with_tag = blob[NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ct_with_tag, None).decode("utf-8")
