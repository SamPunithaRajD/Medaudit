"""Defensive ZIP inspection: path traversal, ZIP bombs, nested archives, encrypted members, type checks."""
from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.document_intelligence.ingestion.validators import MEMBER_EXTENSIONS, sniff


class InvalidArchive(AppError):
    """Invalid archive"""
    status_code, code = 422, "INVALID_ARCHIVE"


@dataclass(frozen=True)
class ZipMember:
    name: str
    kind: str  # pdf | png | jpeg
    size: int
    compressed_size: int


IGNORED_PREFIXES = ("__MACOSX/",)
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _is_unsafe_path(name: str) -> bool:
    if not name or "\x00" in name:
        return True
    norm = name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        return True
    parts = posixpath.normpath(norm).split("/")
    return any(p == ".." for p in parts) or norm.split("/")[0] == ".."


def inspect_zip(data: bytes) -> list[ZipMember]:
    s = get_settings()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InvalidArchive("Archive is corrupt or not a ZIP file") from exc
    members: list[ZipMember] = []
    total = 0
    with zf:
        infos = zf.infolist()
        if len(infos) > s.max_zip_members:
            raise InvalidArchive(f"Archive contains more than {s.max_zip_members} entries")
        for info in infos:
            name = info.filename
            if info.is_dir():
                continue
            if _is_unsafe_path(name):
                raise InvalidArchive("Archive contains an unsafe path", details=[{"field": "file", "issue": "Path traversal"}])
            base = posixpath.basename(name.replace("\\", "/"))
            if name.startswith(IGNORED_PREFIXES) or base in IGNORED_NAMES or base.startswith("._"):
                continue
            if info.flag_bits & 0x1:
                raise InvalidArchive("Encrypted archive members are not supported")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise InvalidArchive("Archive contains symbolic links")
            ext = posixpath.splitext(base.lower())[1]
            if ext == ".zip":
                raise InvalidArchive("Nested archives are not supported")
            kind = MEMBER_EXTENSIONS.get(ext)
            if kind is None:
                raise InvalidArchive(f"Unsupported file in archive: {ext or 'no extension'}",
                                     details=[{"field": "file", "issue": "Unsupported archive member type"}])
            total += info.file_size
            if total > s.max_zip_uncompressed_mb * 1024 * 1024:
                raise InvalidArchive("Archive expands beyond the permitted size")
            ratio = info.file_size / max(info.compress_size, 1)
            if info.file_size > 1024 * 1024 and ratio > s.max_zip_compression_ratio:
                raise InvalidArchive("Archive compression ratio is suspicious (possible ZIP bomb)")
            # Read a bounded amount to verify the signature (never trust declared sizes alone).
            with zf.open(info) as fh:
                head = fh.read(16)
            if sniff(head) != kind:
                raise InvalidArchive(f"Archive member signature mismatch: {base}")
            members.append(ZipMember(name=name, kind=kind, size=info.file_size, compressed_size=info.compress_size))
    if not members:
        raise InvalidArchive("Archive contains no supported PDF or image files")
    members.sort(key=lambda m: _natural_key(m.name))
    return members


def read_member(data: bytes, name: str, max_bytes: int) -> bytes:
    """Read one member with a hard byte cap (defends against lying headers)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open(name) as fh:
        out = fh.read(max_bytes + 1)
    if len(out) > max_bytes:
        raise InvalidArchive("Archive member exceeds the permitted size")
    return out


def _natural_key(s: str):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
