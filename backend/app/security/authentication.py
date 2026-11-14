"""Argon2id password hashing, server-side sessions, CSRF tokens and login verification."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ValidationFailed
from app.models import AuthSettings, User, UserSession
from app.utils.ids import ensure_utc, utcnow

# Argon2id with OWASP-recommended parameters (m=19 MiB, t=2, p=1).
_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1, hash_len=32, salt_len=16)
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")

LOGIN_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,29}$")


def get_auth_settings(db: Session) -> AuthSettings:
    row = db.get(AuthSettings, 1)
    if row is None:
        row = AuthSettings(id=1, activation_token_hours=get_settings().activation_token_expiry)
        db.add(row)
        db.flush()
    return row


def normalize_login_id(value: str) -> str:
    return (value or "").strip().upper()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str, email: str | None = None, login_id: str | None = None,
                               confirm: str | None = None) -> None:
    s = get_settings()
    problems = []
    if confirm is not None and confirm != password:
        problems.append("Passwords do not match")
    if len(password) < s.password_min_length:
        problems.append(f"Password must be at least {s.password_min_length} characters")
    if len(password) > 256:
        problems.append("Password must be at most 256 characters")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        problems.append("Password must contain at least three of: lowercase, uppercase, digit, symbol")
    lowered = password.lower()
    if email and len(email.split("@")[0]) >= 3 and email.split("@")[0].lower() in lowered:
        problems.append("Password must not contain the account name")
    if login_id and login_id.lower() in lowered:
        problems.append("Password must not contain the Doctor ID")
    if problems:
        raise ValidationFailed("Password does not meet policy",
                               details=[{"field": "password", "issue": p} for p in problems])


def _secret() -> bytes:
    s = get_settings().session_secret
    if not s:
        raise RuntimeError("SESSION_SECRET is not configured")
    return s.encode()


def keyed_hash(purpose: str, value: str) -> str:
    return hmac.new(_secret(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def hash_token(token: str) -> str:
    """Session tokens are stored only as keyed hashes so a DB leak does not leak live sessions."""
    return hmac.new(_secret(), token.encode(), hashlib.sha256).hexdigest()


def csrf_token_for(session_id: str) -> str:
    """Per-session CSRF token (double-submit via X-CSRF-Token header), derived so it need not be stored."""
    return keyed_hash("csrf", session_id)[:48]


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hmac.new(_secret(), b"ip:" + ip.encode(), hashlib.sha256).hexdigest()[:32]


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None,
                   remember: bool = False) -> tuple[str, UserSession]:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    now = utcnow()
    ttl = timedelta(days=get_auth_settings(db).remember_me_days) if remember else timedelta(minutes=s.session_ttl_minutes)
    sess = UserSession(user_id=user.id, token_hash=hash_token(token), created_at=now, last_seen_at=now,
                       expires_at=now + ttl, remember=remember, ip_hash=hash_ip(ip),
                       user_agent=(user_agent or "")[:200] or None)
    db.add(sess)
    db.flush()
    return token, sess


def resolve_session(db: Session, token: str | None) -> tuple[User, UserSession] | None:
    if not token or len(token) > 200:
        return None
    s = get_settings()
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if sess is None or sess.revoked_at is not None:
        return None
    now = utcnow()
    if ensure_utc(sess.expires_at) <= now:
        return None
    if not sess.remember and ensure_utc(sess.last_seen_at) + timedelta(minutes=s.session_idle_timeout_minutes) <= now:
        return None
    user = db.get(User, sess.user_id)
    if user is None or user.status != "active" or user.deleted_at is not None:
        return None
    if (now - ensure_utc(sess.last_seen_at)).total_seconds() > 60:
        sess.last_seen_at = now
        db.commit()
    return user, sess


def revoke_session(db: Session, sess: UserSession, reason: str = "logout") -> None:
    sess.revoked_at = utcnow()
    sess.revoked_reason = reason
    db.flush()


def revoke_all_sessions(db: Session, user_id: str, *, except_session_id: str | None = None,
                        reason: str = "revoked") -> int:
    q = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_session_id:
        q = q.where(UserSession.id != except_session_id)
    res = db.execute(q.values(revoked_at=utcnow(), revoked_reason=reason))
    return res.rowcount or 0


@dataclass
class LoginResult:
    user: User | None
    outcome: str  # ok | invalid | locked | suspended | deactivated


def find_user_by_identifier(db: Session, identifier: str) -> User | None:
    ident = (identifier or "").strip()
    if not ident or len(ident) > 255:
        return None
    return db.scalar(select(User).where(
        or_(User.login_id == normalize_login_id(ident), func.lower(User.email) == ident.lower())))


def authenticate(db: Session, identifier: str, password: str) -> LoginResult:
    """Verify credentials. Account state is revealed only after a correct password (no enumeration)."""
    cfg = get_auth_settings(db)
    user = find_user_by_identifier(db, identifier)
    if user is None or user.status == "archived" or user.deleted_at is not None or not user.password_hash:
        verify_password(None, password)  # equalise timing
        return LoginResult(None, "invalid")
    now = utcnow()
    if user.locked_until and ensure_utc(user.locked_until) > now:
        verify_password(None, password)
        return LoginResult(None, "locked")
    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        if user.failed_login_count >= cfg.max_failed_logins:
            user.locked_until = now + timedelta(minutes=cfg.lockout_minutes)
            user.failed_login_count = 0
        db.commit()
        return LoginResult(user, "invalid")
    if user.status == "suspended":
        return LoginResult(user, "suspended")
    if user.status in ("deactivated", "disabled", "locked") or user.status != "active":
        return LoginResult(user, "deactivated")
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return LoginResult(user, "ok")
