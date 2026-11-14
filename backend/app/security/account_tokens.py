"""One-time activation and password-reset codes.

Codes are 12 characters from an unambiguous alphabet (≈60 bits), shown once to the issuing
administrator and stored only as an HMAC bound to the user and purpose. Codes expire, are
single-use, and are invalidated after too many wrong attempts.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AccountToken, User
from app.security.authentication import get_auth_settings, keyed_hash
from app.utils.ids import ensure_utc, utcnow

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PURPOSES = ("activation", "password_reset")


def _normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _hash(user_id: str, purpose: str, code: str) -> str:
    return keyed_hash(f"account-token:{purpose}:{user_id}", _normalise(code))


def issue(db: Session, user: User, purpose: str, created_by: str | None) -> tuple[str, AccountToken]:
    """Revoke outstanding tokens of this purpose and issue a new one. Returns (plain code, row)."""
    assert purpose in PURPOSES
    cfg = get_auth_settings(db)
    now = utcnow()
    db.execute(update(AccountToken).where(AccountToken.user_id == user.id, AccountToken.purpose == purpose,
                                          AccountToken.used_at.is_(None), AccountToken.revoked_at.is_(None))
               .values(revoked_at=now))
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    hours = cfg.activation_token_hours if purpose == "activation" else cfg.reset_token_hours
    row = AccountToken(user_id=user.id, purpose=purpose, token_hash=_hash(user.id, purpose, code),
                       expires_at=now + timedelta(hours=hours), created_by=created_by)
    db.add(row)
    db.flush()
    return code, row


def current(db: Session, user_id: str, purpose: str) -> AccountToken | None:
    return db.scalar(select(AccountToken).where(
        AccountToken.user_id == user_id, AccountToken.purpose == purpose, AccountToken.used_at.is_(None),
        AccountToken.revoked_at.is_(None)).order_by(AccountToken.created_at.desc()))


def verify(db: Session, user: User, purpose: str, code: str) -> AccountToken | None:
    """Return the matching live token, or None. Wrong codes count against the token."""
    tok = current(db, user.id, purpose)
    if tok is None or ensure_utc(tok.expires_at) <= utcnow():
        return None
    if not hmac.compare_digest(tok.token_hash, _hash(user.id, purpose, code)):
        tok.failed_attempts += 1
        if tok.failed_attempts >= get_auth_settings(db).max_activation_attempts:
            tok.revoked_at = utcnow()
        db.commit()
        return None
    return tok
