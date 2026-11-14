"""Opaque, prefixed identifiers and time helpers."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6).upper()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = ensure_utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None
