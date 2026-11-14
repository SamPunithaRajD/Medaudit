"""Account helpers shared by auth, admin and doctor routers."""
from __future__ import annotations

import secrets

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AccountToken, User, UserPreference, UserSession
from app.security.access_control import PERMISSIONS
from app.security.authentication import LOGIN_ID_RE, csrf_token_for, normalize_login_id
from app.utils.ids import ensure_utc, iso, utcnow

HOME = {"admin": "/admin", "doctor": "/doctor"}


def home_for(role: str) -> str:
    return HOME.get(role, "/")


def user_payload(u: User) -> dict:
    p = u.doctor_profile
    return {"id": u.id, "login_id": u.login_id, "email": u.email, "display_name": u.display_name, "role": u.role,
            "department": u.department, "status": u.status, "is_activated": u.is_activated,
            "must_change_password": u.must_change_password, "last_login_at": iso(u.last_login_at),
            "designation": p.designation if p else None, "home": home_for(u.role),
            "permissions": sorted(k for k, roles in PERMISSIONS.items() if u.role in roles)}


def activation_state(db: Session, u: User) -> str:
    if u.is_activated:
        return "activated"
    tok = db.scalar(select(AccountToken).where(AccountToken.user_id == u.id, AccountToken.purpose == "activation")
                    .order_by(AccountToken.created_at.desc()))
    if tok is None or tok.revoked_at is not None:
        return "no_active_code"
    if ensure_utc(tok.expires_at) <= utcnow():
        return "code_expired"
    return "pending"


def doctor_payload(db: Session, u: User, *, detailed: bool = False) -> dict:
    p = u.doctor_profile
    out = {"user_id": u.id, "doctor_id": u.login_id, "full_name": u.display_name, "email": u.email,
           "phone": p.phone if p else None, "department": u.department, "designation": p.designation if p else None,
           "employee_ref": p.employee_ref if p else None, "valid_until": p.valid_until.isoformat() if p and p.valid_until else None,
           "account_status": u.status, "status_reason": u.status_reason, "is_activated": u.is_activated,
           "activation_status": activation_state(db, u), "activated_at": iso(u.activated_at),
           "password_reset_requested_at": iso(p.password_reset_requested_at) if p else None,
           "created_at": iso(u.created_at), "updated_at": iso(u.updated_at), "last_login_at": iso(u.last_login_at),
           "created_by": p.created_by if p else None}
    if detailed:
        out["contact_info"] = p.contact_info if p else None
    return out


def session_payload(s: UserSession, current_id: str | None = None) -> dict:
    return {"id": s.id, "created_at": iso(s.created_at), "last_activity_at": iso(s.last_seen_at),
            "expires_at": iso(s.expires_at), "revoked_at": iso(s.revoked_at), "revoked_reason": s.revoked_reason,
            "remember": s.remember, "user_agent": s.user_agent, "current": s.id == current_id,
            "active": s.revoked_at is None and ensure_utc(s.expires_at) > utcnow()}


def preferences(db: Session, user_id: str) -> UserPreference:
    pref = db.get(UserPreference, user_id)
    if pref is None:
        pref = UserPreference(user_id=user_id, theme="system", language="en",
                              notification_preferences={"case_processed": True, "review_assigned": True,
                                                        "security_alerts": True},
                              dashboard_preferences={"show_recent_reports": True, "default_case_filter": "all"})
        db.add(pref)
        db.flush()
    return pref


def preferences_payload(p: UserPreference) -> dict:
    return {"theme": p.theme, "language": p.language, "notification_preferences": p.notification_preferences or {},
            "dashboard_preferences": p.dashboard_preferences or {}, "updated_at": iso(p.updated_at)}


def set_session_cookie(response: Response, token: str, remember: bool, max_age_seconds: int) -> None:
    s = get_settings()
    response.set_cookie(s.session_cookie_name, token, max_age=max_age_seconds if remember else None, httponly=True,
                        secure=s.session_cookie_secure, samesite=s.session_cookie_samesite, path="/")


def login_response(user: User, sess: UserSession, token: str, native: bool) -> dict:
    session = {"id": sess.id, "expires_at": iso(sess.expires_at), "remember": sess.remember}
    if native:
        session["token"] = token
    return {"user": user_payload(user), "session": session, "csrf_token": csrf_token_for(sess.id),
            "redirect_to": home_for(user.role)}


def generate_doctor_id(db: Session) -> str:
    for _ in range(50):
        candidate = f"DOC{secrets.randbelow(900000) + 100000}"
        if db.scalar(select(User.id).where(User.login_id == candidate)) is None:
            return candidate
    raise RuntimeError("Could not allocate a Doctor ID")


def valid_login_id(value: str) -> str | None:
    v = normalize_login_id(value)
    return v if LOGIN_ID_RE.match(v) else None
