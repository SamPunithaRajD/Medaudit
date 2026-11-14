"""Authentication: sign-in by Doctor ID (or email for staff), activation, password reset, sessions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_user, get_db, paginate, pagination, Pagination
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFound, Unauthenticated, ValidationFailed
from app.models import AuditLog, User, UserSession
from app.schemas.requests import (ActivateRequest, ChangePasswordRequest, ForgotPasswordRequest, LoginRequest,
                                  ResetPasswordRequest)
from app.security import account_tokens
from app.security.access_control import CurrentUser
from app.security.audit_logging import record_event
from app.security.authentication import (authenticate, create_session, csrf_token_for, find_user_by_identifier,
                                         get_auth_settings, hash_ip, hash_password, normalize_login_id,
                                         revoke_all_sessions, revoke_session, validate_password_strength,
                                         verify_password)
from app.security.rate_limit import limiter
from app.services.accounts import (login_response, preferences, preferences_payload, session_payload,
                                   set_session_cookie, user_payload)
from app.utils.ids import ensure_utc, iso, utcnow

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

GENERIC_LOGIN = "Invalid Doctor ID or password. If this is your first sign-in, activate your account first."
GENERIC_ACTIVATION = "The activation details are invalid or have expired. Contact your hospital administrator."
GENERIC_RESET = "The reset details are invalid or have expired. Contact your hospital administrator."


class AccountStateError(AppError):
    status_code = 403


def _native(request: Request) -> bool:
    return request.headers.get("x-medaudit-client", "").lower() == "native"


def _start_session(db: Session, request: Request, response: Response, user: User, remember: bool) -> dict:
    token, sess = create_session(db, user, client_ip(request), request.headers.get("user-agent"), remember=remember)
    max_age = int((ensure_utc(sess.expires_at) - utcnow()).total_seconds())
    set_session_cookie(response, token, remember, max_age)
    return login_response(user, sess, token, _native(request))


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request) or "unknown"
    identifier = body.login_id or body.email or ""
    limiter.hit(f"login:ip:{ip}", s.login_rate_limit_per_minute)
    limiter.hit(f"login:id:{identifier.lower()}", s.login_rate_limit_per_minute)
    res = authenticate(db, identifier, body.password)
    target = res.user.id if res.user else None
    if res.outcome in ("invalid", "locked"):
        record_event(db, "auth.login", outcome="failure", ip_hash=hash_ip(ip), target_user_id=target,
                     details={"reason": res.outcome})
        raise Unauthenticated(GENERIC_LOGIN if res.outcome == "invalid"
                              else "Too many attempts. Please wait a few minutes and try again.",
                              code="INVALID_CREDENTIALS")
    if res.outcome == "suspended":
        record_event(db, "auth.login", outcome="denied", ip_hash=hash_ip(ip), target_user_id=target,
                     details={"reason": "suspended"})
        raise AccountStateError("This account is suspended. Contact your hospital administrator.",
                                code="ACCOUNT_SUSPENDED")
    if res.outcome == "deactivated":
        record_event(db, "auth.login", outcome="denied", ip_hash=hash_ip(ip), target_user_id=target,
                     details={"reason": "inactive"})
        raise AccountStateError("This account is not active. Contact your hospital administrator.",
                                code="ACCOUNT_INACTIVE")
    payload = _start_session(db, request, response, res.user, body.remember_me)
    record_event(db, "auth.login", actor=res.user, target_user_id=res.user.id, ip_hash=hash_ip(ip),
                 resource_type="session", resource_id=payload["session"]["id"],
                 details={"remember": body.remember_me}, commit=False)
    db.commit()
    return payload


@router.post("/doctor/activate", status_code=201)
def activate(body: ActivateRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """First-time activation of an administrator-provisioned Doctor ID."""
    ip = client_ip(request) or "unknown"
    doctor_id = normalize_login_id(body.doctor_id)
    limiter.hit(f"activate:ip:{ip}", 10)
    limiter.hit(f"activate:id:{doctor_id}", 5, window_seconds=900)
    user = db.scalar(select(User).where(User.login_id == doctor_id))

    def fail(reason: str):
        record_event(db, "auth.activation", outcome="failure", ip_hash=hash_ip(ip),
                     target_user_id=user.id if user else None, details={"reason": reason})
        raise ValidationFailed(GENERIC_ACTIVATION, code="ACTIVATION_INVALID")

    if user is None or user.role != "doctor" or user.doctor_profile is None:
        fail("unknown_id")
    if user.is_activated:
        fail("already_activated")
    if user.status != "pending_activation" or user.deleted_at is not None:
        fail(f"status_{user.status}")
    prof = user.doctor_profile
    if prof.valid_until and prof.valid_until < utcnow().date():
        fail("provisioning_expired")
    if user.email.lower() != body.email.strip().lower():
        fail("email_mismatch")
    tok = account_tokens.verify(db, user, "activation", body.activation_code)
    if tok is None:
        fail("invalid_or_expired_code")
    validate_password_strength(body.password, user.email, user.login_id, confirm=body.confirm_password)
    now = utcnow()
    tok.used_at = now
    user.password_hash = hash_password(body.password)
    user.password_changed_at = now
    user.is_activated, user.activated_at = True, now
    user.status, user.status_changed_at, user.status_reason = "active", now, None
    user.last_login_at = now
    preferences(db, user.id)  # private workspace defaults
    payload = _start_session(db, request, response, user, remember=False)
    record_event(db, "auth.activation", actor=user, target_user_id=user.id, ip_hash=hash_ip(ip), commit=False)
    db.commit()
    return payload


@router.post("/password/forgot", status_code=202)
def forgot_password(body: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Always returns the same response. Notifies administrators (no email service is configured)."""
    ip = client_ip(request) or "unknown"
    limiter.hit(f"forgot:ip:{ip}", 5)
    user = find_user_by_identifier(db, body.login_id)
    cfg = get_auth_settings(db)
    if user and user.status == "active" and user.doctor_profile and cfg.allow_forgot_password_requests:
        user.doctor_profile.password_reset_requested_at = utcnow()
        record_event(db, "auth.password_reset_requested", target_user_id=user.id, ip_hash=hash_ip(ip), commit=False)
        db.commit()
    return {"message": "If an active account matches, your hospital administrator has been notified and will "
                       "issue a password reset code through a verified channel."}


@router.post("/password/reset")
def reset_password(body: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request) or "unknown"
    limiter.hit(f"reset:ip:{ip}", 10)
    limiter.hit(f"reset:id:{body.login_id.lower()}", 5, window_seconds=900)
    user = find_user_by_identifier(db, body.login_id)
    tok = account_tokens.verify(db, user, "password_reset", body.reset_code) if user and user.status == "active" else None
    if tok is None:
        record_event(db, "auth.password_reset", outcome="failure", ip_hash=hash_ip(ip),
                     target_user_id=user.id if user else None)
        raise ValidationFailed(GENERIC_RESET, code="RESET_INVALID")
    validate_password_strength(body.new_password, user.email, user.login_id, confirm=body.confirm_password)
    now = utcnow()
    tok.used_at = now
    user.password_hash, user.password_changed_at = hash_password(body.new_password), now
    user.failed_login_count, user.locked_until, user.must_change_password = 0, None, False
    if user.doctor_profile:
        user.doctor_profile.password_reset_requested_at = None
    n = revoke_all_sessions(db, user.id, reason="password_reset")
    record_event(db, "auth.password_reset", target_user_id=user.id, ip_hash=hash_ip(ip),
                 details={"sessions_revoked": n}, commit=False)
    db.commit()
    return {"status": "password_reset", "message": "Password updated. Sign in with your new password."}


@router.post("/logout")
def logout(response: Response, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    sess = db.get(UserSession, cu.session_id)
    revoke_session(db, sess, "logout")
    record_event(db, "auth.logout", actor=cu.user, target_user_id=cu.user.id, ip_hash=cu.ip_hash,
                 resource_type="session", resource_id=sess.id, commit=False)
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"status": "logged_out"}


@router.get("/me")
def me(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    sess = db.get(UserSession, cu.session_id)
    return {"user": user_payload(cu.user), "csrf_token": csrf_token_for(cu.session_id),
            "session": {"id": sess.id, "expires_at": iso(sess.expires_at), "remember": sess.remember},
            "preferences": preferences_payload(preferences(db, cu.user.id))}


@router.post("/password/change")
def change_password(body: ChangePasswordRequest, request: Request, cu: CurrentUser = Depends(current_user),
                    db: Session = Depends(get_db)):
    limiter.hit(f"pwchange:{cu.user.id}", 5)
    if not verify_password(cu.user.password_hash, body.current_password):
        record_event(db, "auth.password_change", actor=cu.user, outcome="failure", ip_hash=cu.ip_hash)
        raise ValidationFailed("Current password is incorrect", details=[{"field": "current_password", "issue": "Incorrect"}])
    validate_password_strength(body.new_password, cu.user.email, cu.user.login_id, confirm=body.confirm_password)
    cu.user.password_hash = hash_password(body.new_password)
    cu.user.password_changed_at = utcnow()
    cu.user.must_change_password = False
    n = revoke_all_sessions(db, cu.user.id, except_session_id=cu.session_id, reason="password_change")
    record_event(db, "auth.password_change", actor=cu.user, target_user_id=cu.user.id, ip_hash=cu.ip_hash,
                 details={"other_sessions_revoked": n}, commit=False)
    db.commit()
    return {"status": "password_changed", "other_sessions_revoked": n}


# Backwards-compatible path used by the first backend release.
router.add_api_route("/change-password", change_password, methods=["POST"], include_in_schema=False)


@router.get("/sessions")
def my_sessions(include_inactive: bool = False, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(UserSession).where(UserSession.user_id == cu.user.id).order_by(UserSession.created_at.desc())
    rows = db.scalars(q.limit(50)).all()
    items = [session_payload(s, cu.session_id) for s in rows]
    return {"items": items if include_inactive else [i for i in items if i["active"]]}


@router.delete("/sessions/{session_id}")
def revoke_my_session(session_id: str, cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    sess = db.get(UserSession, session_id)
    if sess is None or sess.user_id != cu.user.id:
        raise NotFound("Session not found", code="SESSION_NOT_FOUND")
    revoke_session(db, sess, "user_revoked")
    record_event(db, "auth.session_revoke", actor=cu.user, target_user_id=cu.user.id, resource_type="session",
                 resource_id=sess.id, commit=False)
    db.commit()
    return {"status": "revoked", "current": session_id == cu.session_id}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(cu: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    n = revoke_all_sessions(db, cu.user.id, except_session_id=cu.session_id, reason="user_revoked_others")
    record_event(db, "auth.session_revoke", actor=cu.user, target_user_id=cu.user.id, details={"revoked": n},
                 commit=False)
    db.commit()
    return {"revoked": n}


@router.get("/activity")
def my_login_activity(p: Pagination = Depends(pagination), cu: CurrentUser = Depends(current_user),
                      db: Session = Depends(get_db)):
    q = (select(AuditLog).where(AuditLog.target_user_id == cu.user.id, AuditLog.action.like("auth.%"))
         .order_by(AuditLog.seq.desc()))
    return paginate(db, q, p, lambda rows: [{"event_id": r.event_id, "event_type": r.action, "outcome": r.outcome,
                                             "occurred_at": iso(r.occurred_at), "details": r.details} for r in rows])

