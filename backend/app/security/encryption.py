"""AES-256-GCM authenticated encryption with a versioned key ring.

Blob format (bytes):
    b"MAE1" | len(key_version):1 byte | key_version (ascii) | nonce:12 bytes | ciphertext||tag(16)

* A fresh 96-bit random nonce is generated for every encryption.
* The GCM tag authenticates ciphertext *and* associated data (AAD), so blobs
  bound to one context (e.g. a document id) cannot be swapped onto another.
* Keys come from the environment (ENCRYPTION_KEYS / ENCRYPTION_KEY), never the DB or source.
* Old key versions stay in the ring for decryption, enabling rotation (see rotate_blob).
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

MAGIC = b"MAE1"
NONCE_LEN = 12


class EncryptionError(Exception):
    """Raised for configuration problems or failed authentication. Messages never include key material."""


class TamperDetected(EncryptionError):
    pass


def generate_key_b64() -> str:
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _decode_key(raw: str) -> bytes:
    raw = raw.strip()
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            key = decoder(raw + "=" * (-len(raw) % 4))
            if len(key) == 32:
                return key
        except Exception:
            continue
    raise EncryptionError("Encryption key must be 32 bytes, base64 encoded")


@dataclass
class KeyRing:
    keys: dict[str, bytes]
    active_version: str

    @classmethod
    def from_settings(cls) -> "KeyRing":
        s = get_settings()
        keys: dict[str, bytes] = {}
        if s.encryption_keys:
            for item in s.encryption_keys.split(","):
                if not item.strip():
                    continue
                version, _, key = item.partition(":")
                keys[version.strip()] = _decode_key(key)
        if s.encryption_key:
            keys.setdefault("v1", _decode_key(s.encryption_key))
        if not keys:
            raise EncryptionError("No encryption key configured (set ENCRYPTION_KEY or ENCRYPTION_KEYS)")
        active = s.encryption_active_key_version if s.encryption_active_key_version in keys else sorted(keys)[-1]
        return cls(keys=keys, active_version=active)


_ring: KeyRing | None = None


def get_key_ring() -> KeyRing:
    global _ring
    if _ring is None:
        _ring = KeyRing.from_settings()
    return _ring


def reset_key_ring() -> None:
    global _ring
    _ring = None


def encrypt(plaintext: bytes, aad: bytes | None = None, ring: KeyRing | None = None) -> bytes:
    ring = ring or get_key_ring()
    version = ring.active_version.encode("ascii")
    if len(version) > 255:
        raise EncryptionError("Key version too long")
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(ring.keys[ring.active_version]).encrypt(nonce, plaintext, aad)
    return MAGIC + bytes([len(version)]) + version + nonce + ct


def parse_header(blob: bytes) -> tuple[str, bytes, bytes]:
    if len(blob) < len(MAGIC) + 1 + NONCE_LEN + 16 or not blob.startswith(MAGIC):
        raise TamperDetected("Ciphertext is malformed")
    vlen = blob[len(MAGIC)]
    start = len(MAGIC) + 1
    version = blob[start:start + vlen].decode("ascii", errors="strict")
    nonce = blob[start + vlen:start + vlen + NONCE_LEN]
    ct = blob[start + vlen + NONCE_LEN:]
    return version, nonce, ct


def decrypt(blob: bytes, aad: bytes | None = None, ring: KeyRing | None = None) -> bytes:
    ring = ring or get_key_ring()
    try:
        version, nonce, ct = parse_header(blob)
    except UnicodeDecodeError as exc:
        raise TamperDetected("Ciphertext is malformed") from exc
    key = ring.keys.get(version)
    if key is None:
        raise EncryptionError(f"Unknown key version '{version}'")
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise TamperDetected("Authentication tag verification failed") from exc


def rotate_blob(blob: bytes, aad: bytes | None = None, ring: KeyRing | None = None) -> bytes:
    """Re-encrypt a blob under the active key version (used by scripts/rotate_keys.py)."""
    ring = ring or get_key_ring()
    version, _, _ = parse_header(blob)
    if version == ring.active_version:
        return blob
    return encrypt(decrypt(blob, aad, ring), aad, ring)


def encrypt_str(value: str, aad: str | None = None) -> str:
    return base64.b64encode(encrypt(value.encode("utf-8"), aad.encode() if aad else None)).decode("ascii")


def decrypt_str(value: str, aad: str | None = None) -> str:
    return decrypt(base64.b64decode(value), aad.encode() if aad else None).decode("utf-8")


# --- Encrypted file storage -------------------------------------------------

def write_encrypted_file(path: os.PathLike | str, data: bytes, aad: str) -> None:
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(encrypt(data, aad.encode()))
    os.replace(tmp, path)


def read_encrypted_file(path: os.PathLike | str, aad: str) -> bytes:
    with open(path, "rb") as fh:
        return decrypt(fh.read(), aad.encode())
