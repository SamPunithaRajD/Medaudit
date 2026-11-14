"""File-type validation by magic bytes (never trusting filename or client MIME alone)."""
from __future__ import annotations

import os
import re
import unicodedata

from app.core.exceptions import UnsupportedMediaType

PDF_MAGIC = b"%PDF-"
ZIP_MAGICS = (b"PK\x03\x04",)  # empty (PK\x05\x06) and spanned archives are rejected
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

UPLOAD_EXTENSIONS = {".pdf": "pdf", ".zip": "zip"}
MEMBER_EXTENSIONS = {".pdf": "pdf", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}
MEDIA_TYPES = {"pdf": "application/pdf", "zip": "application/zip", "png": "image/png", "jpeg": "image/jpeg"}


def sniff(head: bytes) -> str | None:
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if any(head.startswith(m) for m in ZIP_MAGICS):
        return "zip"
    if head.startswith(PNG_MAGIC):
        return "png"
    if head.startswith(JPEG_MAGIC):
        return "jpeg"
    return None


def safe_filename(name: str | None, default: str = "upload") -> str:
    name = os.path.basename((name or default).replace("\\", "/"))
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .") or default
    return name[:200]


def validate_upload_type(filename: str | None, head: bytes) -> str:
    """Return 'pdf' or 'zip' if both extension and signature agree, else raise 415."""
    ext = os.path.splitext((filename or "").lower())[1]
    expected = UPLOAD_EXTENSIONS.get(ext)
    actual = sniff(head)
    if expected is None:
        raise UnsupportedMediaType("Only PDF or ZIP uploads are supported", code="UNSUPPORTED_MEDIA_TYPE",
                                   details=[{"field": "file", "issue": "Unsupported file extension"}])
    if actual != expected:
        raise UnsupportedMediaType("File content does not match its extension", code="UNSUPPORTED_MEDIA_TYPE",
                                   details=[{"field": "file", "issue": "File signature mismatch"}])
    return expected
