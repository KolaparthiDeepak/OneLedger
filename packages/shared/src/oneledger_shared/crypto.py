"""Authenticated encryption and keyed hashing.

Uses AES-256-GCM from the maintained ``cryptography`` library. Ciphertexts carry the
key version so keys can be rotated: ``v{version}:{nonce}{ciphertext}`` (base64).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = b"OL1"


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _decode_key(key: str) -> bytes:
    raw = base64.urlsafe_b64decode(key.encode())
    if len(raw) != 32:
        raise ValueError("encryption key must be 32 bytes")
    return raw


class SecretBox:
    """Encrypts small secrets and blobs with a versioned key ring."""

    def __init__(self, current_key: str, current_version: int, previous: dict[int, str] | None = None):
        self._version = current_version
        self._keys = {current_version: _decode_key(current_key)}
        for version, key in (previous or {}).items():
            self._keys.setdefault(version, _decode_key(key))

    @property
    def version(self) -> int:
        return self._version

    def encrypt(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self._version]).encrypt(nonce, plaintext, associated_data)
        return _PREFIX + self._version.to_bytes(2, "big") + nonce + ct

    def decrypt(self, blob: bytes, associated_data: bytes = b"") -> bytes:
        if not blob.startswith(_PREFIX):
            raise ValueError("unrecognised ciphertext format")
        version = int.from_bytes(blob[3:5], "big")
        key = self._keys.get(version)
        if key is None:
            raise ValueError("ciphertext key version is not available")
        return AESGCM(key).decrypt(blob[5:17], blob[17:], associated_data)

    def key_version_of(self, blob: bytes) -> int:
        return int.from_bytes(blob[3:5], "big")

    def encrypt_str(self, value: str, associated_data: bytes = b"") -> bytes:
        return self.encrypt(value.encode(), associated_data)

    def decrypt_str(self, blob: bytes, associated_data: bytes = b"") -> str:
        return self.decrypt(blob, associated_data).decode()


def keyed_hash(key: str, value: str) -> str:
    """HMAC-SHA256 used for session/API token lookup; tokens themselves are never stored."""
    return hmac.new(_decode_key(key), value.encode(), hashlib.sha256).hexdigest()


def new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_key_ring(spec: str) -> dict[int, str]:
    """Parse ``PREVIOUS_SECRET_ENCRYPTION_KEYS`` formatted as ``1:key,2:key``."""
    ring: dict[int, str] = {}
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        version, _, key = part.partition(":")
        ring[int(version)] = key
    return ring
