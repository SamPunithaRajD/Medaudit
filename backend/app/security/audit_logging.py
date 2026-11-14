"""Append-only, HMAC hash-chained audit events.

Each row's hash = HMAC(key, prev_hash || canonical(row)). Any modification, deletion or
re-ordering of historical rows breaks the chain, which `verify_chain` detects.
The ORM refuses UPDATE/DELETE on AuditLog; on PostgreSQL a trigger (see migrations) enforces the same.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import request_id_var
from app.models import AuditLog
from app.utils.ids import iso, new_id, utcnow

GENESIS = "0" * 64
_lock = threading.Lock()
_FORBIDDEN_DETAIL_KEYS = {"patient_name", "patient_identifier", "text", "excerpt", "content", "password", "token",
                         "code", "activation_code", "reset_code", "new_password", "session_token", "csrf_token"}


def _key() -> bytes:
    s = get_settings()
    raw = s.audit_log_hmac_key or s.session_secret
    if not raw:
        raise RuntimeError("AUDIT_LOG_HMAC_KEY or SESSION_SECRET must be configured")
    return hashlib.sha256(b"medaudit-audit-log:" + raw.encode()).digest()


def _canonical(row: AuditLog) -> bytes:
    payload = {
        "event_id": row.event_id, "occurred_at": iso(row.occurred_at), "actor_id": row.actor_id,
        "actor_role": row.actor_role, "action": row.action, "resource_type": row.resource_type,
        "resource_id": row.resource_id, "case_id": row.case_id, "outcome": row.outcome,
        "ip_hash": row.ip_hash, "request_id": row.request_id, "details": row.details or {},
    }
    if row.target_user_id is not None:  # added in schema v2; omitted when null so older rows still verify
        payload["target_user_id"] = row.target_user_id
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _compute(prev_hash: str, row: AuditLog) -> str:
    return hmac.new(_key(), prev_hash.encode() + b"|" + _canonical(row), hashlib.sha256).hexdigest()


def _sanitize(details: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in (details or {}).items():
        if k in _FORBIDDEN_DETAIL_KEYS:
            continue
        clean[k] = v if isinstance(v, (int, float, bool, type(None))) else str(v)[:200]
    return clean


def record_event(db: Session, action: str, *, actor=None, resource_type: str | None = None,
                 resource_id: str | None = None, case_id: str | None = None, outcome: str = "success",
                 target_user_id: str | None = None,
                 ip_hash: str | None = None, details: dict[str, Any] | None = None, commit: bool = True) -> AuditLog:
    """Append an audit event. Commits by default so denied/failed attempts are captured even if the request fails."""
    with _lock:
        prev = db.scalar(select(AuditLog.row_hash).order_by(AuditLog.seq.desc()).limit(1)) or GENESIS
        row = AuditLog(event_id=new_id("AUD"), occurred_at=utcnow().replace(microsecond=0),
                       actor_id=getattr(actor, "id", None), actor_role=getattr(actor, "role", None), action=action,
                       resource_type=resource_type, resource_id=resource_id, case_id=case_id, outcome=outcome,
                       target_user_id=target_user_id,
                       ip_hash=ip_hash, request_id=request_id_var.get(), details=_sanitize(details), prev_hash=prev)
        row.row_hash = _compute(prev, row)
        db.add(row)
        if commit:
            db.commit()
        else:
            db.flush()
    return row


def verify_chain(db: Session) -> dict[str, Any]:
    prev = GENESIS
    count = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.seq.asc())).yield_per(500):
        count += 1
        if row.prev_hash != prev or not hmac.compare_digest(row.row_hash, _compute(prev, row)):
            return {"valid": False, "checked": count, "broken_at_event": row.event_id}
        prev = row.row_hash
    return {"valid": True, "checked": count, "broken_at_event": None}
