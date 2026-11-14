"""De-identification utilities for direct identifiers in free text and structured records."""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from app.core.config import get_settings

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("NAME", re.compile(r"(?im)^(\s*(?:patient\s*name|name\s*of\s*patient|name)\s*[:\-]\s*)(.+?)\s*$")),
    ("ID", re.compile(r"(?i)\b((?:UHID|MRN|IP\s*No\.?|IPD\s*No\.?|Reg(?:istration)?\s*No\.?|Patient\s*ID|Aadhaar|ABHA)\s*[:\-#]?\s*)([A-Z0-9][A-Z0-9\-/]{3,})")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9}\d(?!\d)")),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ADDRESS", re.compile(r"(?im)^(\s*(?:address|addr|residence)\s*[:\-]\s*)(.+?)\s*$")),
]


def pseudonym(value: str, prefix: str = "P") -> str:
    """Stable keyed pseudonym (same input → same token) without revealing the input."""
    key = (get_settings().session_secret or "medaudit").encode()
    return f"{prefix}-{hmac.new(key, value.strip().lower().encode(), hashlib.sha256).hexdigest()[:10].upper()}"


def mask_text(text: str, extra_names: list[str] | None = None) -> str:
    if not text:
        return text
    out = text
    for label, pattern in PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda m, lb=label: f"{m.group(1)}[{lb}]", out)
        else:
            out = pattern.sub(f"[{label}]", out)
    for name in extra_names or []:
        if name and len(name.strip()) >= 3:
            out = re.sub(re.escape(name.strip()), "[NAME]", out, flags=re.IGNORECASE)
    return out


DIRECT_IDENTIFIER_FIELDS = {"patient_name", "patient_identifier", "display_name", "phone", "address", "email"}


def deidentify_record(record: dict[str, Any], names: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in record.items():
        if k in DIRECT_IDENTIFIER_FIELDS:
            out[k] = None
        elif k == "patient_ref" and v:
            out[k] = pseudonym(str(v))
        elif isinstance(v, str):
            out[k] = mask_text(v, names)
        elif isinstance(v, dict):
            out[k] = deidentify_record(v, names)
        elif isinstance(v, list):
            out[k] = [deidentify_record(i, names) if isinstance(i, dict) else (mask_text(i, names) if isinstance(i, str) else i) for i in v]
        else:
            out[k] = v
    return out
