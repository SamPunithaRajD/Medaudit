"""Encrypted object storage on the local filesystem (storage/). Keys are opaque, never user-controlled."""
from __future__ import annotations

import os
from pathlib import Path

from app.core.config import get_settings
from app.security.encryption import get_key_ring, read_encrypted_file, write_encrypted_file


def _path(key: str) -> Path:
    if ".." in key or key.startswith(("/", "\\")) or ":" in key:
        raise ValueError("Invalid storage key")
    return get_settings().storage_path / key


def put(key: str, data: bytes, aad: str) -> str:
    write_encrypted_file(_path(key), data, aad)
    return get_key_ring().active_version


def get(key: str, aad: str) -> bytes:
    return read_encrypted_file(_path(key), aad)


def delete(key: str) -> None:
    p = _path(key)
    if p.exists():
        os.remove(p)


def document_key(document_id: str) -> str:
    return f"documents/{document_id}/original.enc"
