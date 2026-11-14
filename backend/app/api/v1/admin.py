"""Hospital administration: doctor provisioning and account lifecycle, staff users, settings, security logs.
Administrators manage accounts; this router never returns clinical record content."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.core.config import get_settings
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.models import AccountToken, AuditLog, DoctorProfile, User, UserSession
from app.models.enums import Role
from app.schemas.requests import (AuthSettingsUpdate, DoctorCreate, DoctorUpdate, OptionalReason,
                                  PasswordResetInitiate, ResetActivation, StatusChange, UserCreate, UserUpdate)
from app.security import account_tokens
from app.security.access_control import CurrentUser
from app.security.audit_logging import record_event, verify_chain
from app.security.authentication import (get_auth_settings, hash_password, normalize_login_id, revoke_all_sessions,
                                         validate_password_strength)
from app.services.accounts import (doctor_payload, generate_doctor_id, session_payload, user_payload,
                                   valid_login_id)
from app.utils.ids import ensure_utc, iso, utcnow

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
ADMIN = require("admin.doctors")
DOCTOR_STATUSES = ("pending_activation", "active", "suspended", "deactivated", "archived")
SORTS = {"doctor_id": User.login_id, "full_name": User.display_name, "department": User.department,
         "created_at": User.created_at, "last_login_at": User.last_login_at, "status": User.status}
CSV_FIELDS = ["doctor_id", "full_name", "email", "phone", "department", "designation", "employee_ref", "valid_until"]


def _activation_info(code: str, tok: AccountToken, user: User) -> dict:
    base = get_settings().app_base_url.rstrip("/")
    return {"activation_code": code, "expires_at": iso(tok.expires_at),
            "activation_url": f"{base}/activate?doctor_id={user.login_id}",
            "notice": "Shown once. Deliver the code to the doctor through a verified hospital channel; "
                      "it is not stored in readable form."}


def _doctor(db: Session, doctor_id: str) -> User:
    u = db.scalar(select(User).where(User.login_id == normalize_login_id(doctor_id), User.role == Role.doctor))
    if u is None or u.doctor_profile is None:
        raise NotFound("Doctor not found", code="DOCTOR_NOT_FOUND")
    return u


def _check_conflicts(db: Session, *, login_id: str | None = None, email: str | None = None,
                     employee_ref: str | None = None, exclude_user: str | None = None) -> list[dict]:
    issues = []
    def taken(q):
        if exclude_user:
            q = q.where(User.id != exclude_user)
        return db.scalar(q) is not None
    if login_id and taken(select(User.id).where(User.login_id == login_id)):
        issues.append({"field": "doctor_id", "issue": "Doctor ID already exists"})
    if email and taken(select(User.id).where(func.lower(User.email) == email.lower())):
        issues.append({"field": "email", "issue": "Email is already registered"})
    if employee_ref:
        q = select(DoctorProfile.id).where(DoctorProfile.employee_ref == employee_ref)
        if exclude_user:
            q = q.where(DoctorProfile.user_id != exclude_user)
        if db.scalar(q) is not None:
            issues.append({"field": "employee_ref", "issue": "Employee reference already used"})
    return issues


def _create_doctor(db: Session, cu: CurrentUser, body: DoctorCreate) -> tuple[User, str, AccountToken]:
    if body.doctor_id:
        login_id = valid_login_id(body.doctor_id)
        if not login_id:
            raise ValidationFailed("Invalid Doctor ID", details=[{"field": "doctor_id",
                                                                  "issue": "Use 3–30 letters, digits or hyphens"}])
    else:
        login_id = generate_doctor_id(db)
    issues = _check_conflicts(db, login_id=login_id, email=body.email, employee_ref=body.employee_ref)
    if issues:
        raise Conflict("Doctor ID, email or employee reference already exists", code="DOCTOR_CONFLICT", details=issues)
    if body.valid_until and body.valid_until < date.today():
        raise ValidationFailed("Validity date is in the past", details=[{"field": "valid_until", "issue": "Past date"}])
    user = User(login_id=login_id, email=body.email.strip().lower(), display_name=body.full_name.strip(),
                role=Role.doctor, department=body.department.strip(), password_hash=None, status="pending_activation",
                is_activated=False, status_changed_at=utcnow())
    db.add(user)
    db.flush()
    db.add(DoctorProfile(user_id=user.id, designation=body.designation, phone=body.phone,
                         employee_ref=body.employee_ref or None, valid_until=body.valid_until, created_by=cu.user.id))
    db.flush()
    code, tok = account_tokens.issue(db, user, "activation", cu.user.id)
    record_event(db, "doctor.create", actor=cu.user, target_user_id=user.id, resource_type="doctor",
                 resource_id=login_id, ip_hash=cu.ip_hash, commit=False)
    db.refresh(user)
    return user, code, tok


# --- dashboard -----------------------------------------------------------------------

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    doctors = select(User).where(User.role == Role.doctor)
    counts = dict(db.execute(select(User.status, func.count()).where(User.role == Role.doctor).group_by(User.status)).all())
    week = utcnow() - timedelta(days=7)
    recent_reg = db.scalars(doctors.where(User.is_activated.is_(True)).order_by(User.activated_at.desc()).limit(5)).all()
    recent_login = db.scalars(doctors.where(User.last_login_at.is_not(None)).order_by(User.last_login_at.desc()).limit(5)).all()
    sec = db.scalars(select(AuditLog).where(or_(AuditLog.outcome != "success", AuditLog.action.like("doctor.%"),
                                                AuditLog.action.in_(["auth.password_reset_requested",
                                                                     "clinical.emergency_access"])))
                     .order_by(AuditLog.seq.desc()).limit(10)).all()
    reset_requests = db.scalar(select(func.count()).select_from(DoctorProfile)
                               .where(DoctorProfile.password_reset_requested_at.is_not(None))) or 0
    return {
        "total_doctor_ids": sum(counts.values()),
        "activated_accounts": db.scalar(select(func.count()).select_from(User).where(
            User.role == Role.doctor, User.is_activated.is_(True))) or 0,
        "pending_activations": counts.get("pending_activation", 0),
        "active_doctors": counts.get("active", 0),
        "suspended_accounts": counts.get("suspended", 0),
        "deactivated_accounts": counts.get("deactivated", 0),
        "archived_accounts": counts.get("archived", 0),
        "password_reset_requests": reset_requests,
        "failed_logins_7d": db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "auth.login", AuditLog.outcome != "success", AuditLog.occurred_at >= week)) or 0,
        "logins_7d": db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "auth.login", AuditLog.outcome == "success", AuditLog.actor_role == Role.doctor,
            AuditLog.occurred_at >= week)) or 0,
        "recent_registrations": [{"doctor_id": u.login_id, "full_name": u.display_name, "activated_at": iso(u.activated_at)}
                                 for u in recent_reg],
        "recent_logins": [{"doctor_id": u.login_id, "full_name": u.display_name, "last_login_at": iso(u.last_login_at)}
                          for u in recent_login],
        "security_events": [_event(r) for r in sec],
    }


def _event(r: AuditLog) -> dict:
    return {"event_id": r.event_id, "event_type": r.action, "actor_id": r.actor_id, "actor_role": r.actor_role,
            "target_user_id": r.target_user_id, "resource_type": r.resource_type, "resource_id": r.resource_id,
            "case_id": r.case_id, "outcome": r.outcome, "occurred_at": iso(r.occurred_at), "metadata": r.details}


# --- doctor directory ------------------------------------------------------------------

def _directory_query(q: str | None, status: str | None, department: str | None):
    query = select(User).where(User.role == Role.doctor)
    if status and status != "all":
        if status not in DOCTOR_STATUSES:
            raise ValidationFailed("Unknown status filter", details=[{"field": "status", "issue": "Invalid"}])
        query = query.where(User.status == status)
    elif not status or status == "all":
        query = query.where(User.status != "archived")
    if department:
        query = query.where(func.lower(User.department) == department.lower())
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(User.login_id).like(like), func.lower(User.display_name).like(like),
                                func.lower(User.department).like(like), func.lower(User.email).like(like)))
    return query


@router.get("/doctors")
def list_doctors(p: Pagination = Depends(pagination), q: str | None = Query(None, max_length=100),
                 status: str | None = None, department: str | None = None, sort: str = "created_at:desc",
                 db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    field, _, direction = sort.partition(":")
    col = SORTS.get(field)
    if col is None:
        raise ValidationFailed("Unsupported sort field", details=[{"field": "sort", "issue": f"Use one of {sorted(SORTS)}"}])
    query = _directory_query(q, status, department).order_by(col.asc() if direction == "asc" else col.desc(), User.id)
    res = paginate(db, query, p, lambda rows: [doctor_payload(db, u) for u in rows])
    res["departments"] = sorted(d for (d,) in db.execute(select(User.department).where(
        User.role == Role.doctor, User.department.is_not(None)).distinct()).all())
    return res


@router.post("/doctors", status_code=201)
def create_doctor(body: DoctorCreate, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    user, code, tok = _create_doctor(db, cu, body)
    db.commit()
    return {"doctor": doctor_payload(db, user), "activation": _activation_info(code, tok, user)}


@router.get("/doctors/export", response_class=Response)
def export_doctors(q: str | None = None, status: str | None = None, db: Session = Depends(get_db),
                   cu: CurrentUser = Depends(ADMIN)):
    rows = db.scalars(_directory_query(q, status, None).order_by(User.login_id)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS + ["account_status", "is_activated", "created_at", "last_login_at"])
    for u in rows:
        d = doctor_payload(db, u)
        w.writerow([_csv_safe(d.get(k)) for k in CSV_FIELDS] +
                   [d["account_status"], d["is_activated"], d["created_at"], d["last_login_at"]])
    record_event(db, "doctor.export", actor=cu.user, ip_hash=cu.ip_hash, details={"rows": len(rows)})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="doctor-directory.csv"', "Cache-Control": "no-store"})


def _csv_safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # spreadsheet formula injection guard


@router.get("/doctors/import-template", response_class=Response)
def import_template(cu: CurrentUser = Depends(ADMIN)):
    return Response(",".join(CSV_FIELDS) + "\n,Dr. Example Name,example@hospital.local,,General Medicine,Consultant,,\n",
                    media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="doctor-import-template.csv"'})


@router.post("/doctors/import")
def import_doctors(file: UploadFile = File(...), commit: bool = False, db: Session = Depends(get_db),
                   cu: CurrentUser = Depends(ADMIN)):
    """Validate a CSV (preview). With commit=true, all rows are created atomically only if every row is valid."""
    raw = file.file.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValidationFailed("CSV larger than 1 MB", code="FILE_TOO_LARGE")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed("CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"full_name", "email", "department"} <= set(reader.fieldnames):
        raise ValidationFailed("CSV header must include full_name, email and department",
                               details=[{"field": "file", "issue": f"Expected columns: {', '.join(CSV_FIELDS)}"}])
    rows, seen_ids, seen_emails = [], set(), set()
    for i, rec in enumerate(reader, start=2):
        if i > 1001:
            raise ValidationFailed("Maximum 1000 rows per import")
        rec = {k: (rec.get(k) or "").strip() for k in CSV_FIELDS}
        errors: list[str] = []
        try:
            body = DoctorCreate(doctor_id=rec["doctor_id"] or None, full_name=rec["full_name"], email=rec["email"],
                                phone=rec["phone"] or None, department=rec["department"],
                                designation=rec["designation"] or None, employee_ref=rec["employee_ref"] or None,
                                valid_until=rec["valid_until"] or None)
        except Exception as exc:
            body = None
            errors += [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in getattr(exc, "errors", lambda: [])()] or ["Invalid row"]
        if body is not None:
            lid = valid_login_id(body.doctor_id) if body.doctor_id else None
            if body.doctor_id and not lid:
                errors.append("doctor_id: invalid format")
            errors += [f"{x['field']}: {x['issue']}" for x in _check_conflicts(db, login_id=lid, email=body.email,
                                                                             employee_ref=body.employee_ref)]
            if lid and lid in seen_ids:
                errors.append("doctor_id: duplicated in file")
            if body.email.lower() in seen_emails:
                errors.append("email: duplicated in file")
            seen_ids.add(lid)
            seen_emails.add(body.email.lower())
        rows.append({"row": i, "data": rec, "errors": errors, "valid": not errors, "_body": body})
    invalid = [r for r in rows if not r["valid"]]
    preview = [{k: v for k, v in r.items() if k != "_body"} for r in rows]
    if not commit or invalid:
        return {"committed": False, "total_rows": len(rows), "valid_rows": len(rows) - len(invalid),
                "invalid_rows": len(invalid), "rows": preview}
    created = []
    for r in rows:
        user, code, tok = _create_doctor(db, cu, r["_body"])
        created.append({"row": r["row"], "doctor_id": user.login_id, "full_name": user.display_name,
                        **_activation_info(code, tok, user)})
    record_event(db, "doctor.import", actor=cu.user, ip_hash=cu.ip_hash, details={"created": len(created)}, commit=False)
    db.commit()
    return {"committed": True, "total_rows": len(rows), "created": created}


@router.get("/doctors/{doctor_id}")
def doctor_detail(doctor_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    tokens = db.scalars(select(AccountToken).where(AccountToken.user_id == u.id).order_by(AccountToken.created_at.desc())).all()
    sessions = db.scalars(select(UserSession).where(UserSession.user_id == u.id).order_by(UserSession.created_at.desc()).limit(20)).all()
    events = db.scalars(select(AuditLog).where(AuditLog.target_user_id == u.id).order_by(AuditLog.seq.desc()).limit(50)).all()
    record_event(db, "doctor.view", actor=cu.user, target_user_id=u.id, resource_type="doctor", resource_id=u.login_id)
    now = utcnow()
    return {
        "doctor": doctor_payload(db, u, detailed=True),
        "activation_history": [{"id": t.id, "purpose": t.purpose, "created_at": iso(t.created_at), "expires_at": iso(t.expires_at),
                                "used_at": iso(t.used_at), "revoked_at": iso(t.revoked_at), "failed_attempts": t.failed_attempts,
                                "state": "used" if t.used_at else "revoked" if t.revoked_at else
                                ("expired" if ensure_utc(t.expires_at) <= now else "active")} for t in tokens],
        "active_sessions": [session_payload(s) for s in sessions if s.revoked_at is None and ensure_utc(s.expires_at) > now],
        "security_events": [_event(e) for e in events if e.action.startswith("auth.")],
        "administrative_changes": [_event(e) for e in events if e.action.startswith("doctor.") and e.action != "doctor.view"],
    }


@router.patch("/doctors/{doctor_id}")
def update_doctor(doctor_id: str, body: DoctorUpdate, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    changes = body.model_dump(exclude_unset=True)
    expected = changes.pop("expected_updated_at", None)
    if expected and ensure_utc(expected).replace(microsecond=0) != ensure_utc(u.updated_at).replace(microsecond=0):
        raise Conflict("This record changed since you loaded it; refresh and retry", code="DOCTOR_CONFLICT")
    issues = _check_conflicts(db, email=changes.get("email"), employee_ref=changes.get("employee_ref"), exclude_user=u.id)
    if issues:
        raise Conflict("Email or employee reference already exists", code="DOCTOR_CONFLICT", details=issues)
    p = u.doctor_profile
    for k, v in changes.items():
        if k == "full_name":
            u.display_name = v
        elif k == "email":
            u.email = v.lower()
        elif k == "department":
            u.department = v
        else:
            setattr(p, k, v)
    u.updated_at = utcnow()
    record_event(db, "doctor.update", actor=cu.user, target_user_id=u.id, resource_type="doctor", resource_id=u.login_id,
                 details={"fields": ",".join(sorted(changes))}, commit=False)
    db.commit()
    return {"doctor": doctor_payload(db, u)}


def _set_status(db: Session, cu: CurrentUser, u: User, status: str, reason: str | None, action: str) -> dict:
    u.status, u.status_reason, u.status_changed_at = status, reason, utcnow()
    n = revoke_all_sessions(db, u.id, reason=action) if status != "active" else 0
    record_event(db, action, actor=cu.user, target_user_id=u.id, resource_type="doctor", resource_id=u.login_id,
                 ip_hash=cu.ip_hash, details={"reason": reason, "sessions_revoked": n}, commit=False)
    db.commit()
    return {"doctor": doctor_payload(db, u), "sessions_revoked": n}


@router.post("/doctors/{doctor_id}/suspend")
def suspend(doctor_id: str, body: StatusChange, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    if u.status in ("archived", "suspended"):
        raise Conflict(f"Account is {u.status}", code="INVALID_STATE")
    return _set_status(db, cu, u, "suspended", body.reason, "doctor.suspend")


@router.post("/doctors/{doctor_id}/deactivate")
def deactivate(doctor_id: str, body: StatusChange, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    if u.status in ("archived", "deactivated"):
        raise Conflict(f"Account is {u.status}", code="INVALID_STATE")
    return _set_status(db, cu, u, "deactivated", body.reason, "doctor.deactivate")


@router.post("/doctors/{doctor_id}/reactivate")
def reactivate(doctor_id: str, body: OptionalReason | None = None, db: Session = Depends(get_db),
               cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    if u.status not in ("suspended", "deactivated", "archived"):
        raise Conflict("Account is not suspended, deactivated or archived", code="INVALID_STATE")
    target = "active" if u.is_activated else "pending_activation"
    u.deleted_at = None
    return _set_status(db, cu, u, target, body.reason if body else None, "doctor.reactivate")


@router.delete("/doctors/{doctor_id}")
def archive(doctor_id: str, reason: str = Query(..., min_length=3, max_length=500), db: Session = Depends(get_db),
            cu: CurrentUser = Depends(ADMIN)):
    """Soft archive. Patients, cases and audit history are retained (no cascade delete)."""
    u = _doctor(db, doctor_id)
    if u.status == "archived":
        raise Conflict("Account already archived", code="INVALID_STATE")
    db.query(AccountToken).filter(AccountToken.user_id == u.id, AccountToken.used_at.is_(None),
                                  AccountToken.revoked_at.is_(None)).update({"revoked_at": utcnow()})
    return _set_status(db, cu, u, "archived", reason, "doctor.archive")


@router.post("/doctors/{doctor_id}/resend-activation")
def resend_activation(doctor_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    if u.is_activated:
        raise Conflict("Account is already activated", code="ALREADY_ACTIVATED")
    if u.status != "pending_activation":
        raise Conflict(f"Account is {u.status}; reactivate it first", code="INVALID_STATE")
    code, tok = account_tokens.issue(db, u, "activation", cu.user.id)
    record_event(db, "doctor.activation_resend", actor=cu.user, target_user_id=u.id, resource_type="doctor",
                 resource_id=u.login_id, ip_hash=cu.ip_hash, commit=False)
    db.commit()
    return {"doctor": doctor_payload(db, u), "activation": _activation_info(code, tok, u)}


@router.post("/doctors/{doctor_id}/reset-activation")
def reset_activation(doctor_id: str, body: ResetActivation, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    """Return an activated account to 'pending activation': clears the password, ends sessions, issues a new code."""
    u = _doctor(db, doctor_id)
    if normalize_login_id(body.confirm_doctor_id) != u.login_id:
        raise ValidationFailed("Confirmation does not match the Doctor ID",
                               details=[{"field": "confirm_doctor_id", "issue": "Type the Doctor ID to confirm"}])
    if u.status == "archived":
        raise Conflict("Archived accounts cannot be reset", code="INVALID_STATE")
    u.password_hash, u.is_activated, u.activated_at = None, False, None
    u.status, u.status_reason, u.status_changed_at = "pending_activation", body.reason, utcnow()
    n = revoke_all_sessions(db, u.id, reason="activation_reset")
    code, tok = account_tokens.issue(db, u, "activation", cu.user.id)
    record_event(db, "doctor.activation_reset", actor=cu.user, target_user_id=u.id, resource_type="doctor",
                 resource_id=u.login_id, ip_hash=cu.ip_hash, details={"reason": body.reason, "sessions_revoked": n},
                 commit=False)
    db.commit()
    return {"doctor": doctor_payload(db, u), "activation": _activation_info(code, tok, u)}


@router.post("/doctors/{doctor_id}/password-reset")
def initiate_password_reset(doctor_id: str, body: PasswordResetInitiate | None = None, db: Session = Depends(get_db),
                            cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    if not u.is_activated or u.status != "active":
        raise Conflict("Password reset is only available for active, activated accounts", code="INVALID_STATE")
    code, tok = account_tokens.issue(db, u, "password_reset", cu.user.id)
    n = revoke_all_sessions(db, u.id, reason="password_reset_initiated") if (body is None or body.revoke_sessions) else 0
    u.doctor_profile.password_reset_requested_at = None
    record_event(db, "auth.password_reset_initiated", actor=cu.user, target_user_id=u.id, resource_type="doctor",
                 resource_id=u.login_id, ip_hash=cu.ip_hash, details={"sessions_revoked": n}, commit=False)
    db.commit()
    base = get_settings().app_base_url.rstrip("/")
    return {"doctor": doctor_payload(db, u), "sessions_revoked": n,
            "password_reset": {"reset_code": code, "expires_at": iso(tok.expires_at),
                               "reset_url": f"{base}/reset-password?login_id={u.login_id}",
                               "notice": "Shown once. Deliver through a verified hospital channel."}}


@router.get("/doctors/{doctor_id}/sessions")
def doctor_sessions(doctor_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    rows = db.scalars(select(UserSession).where(UserSession.user_id == u.id).order_by(UserSession.created_at.desc()).limit(50)).all()
    return {"items": [session_payload(s) for s in rows]}


@router.delete("/doctors/{doctor_id}/sessions")
def revoke_doctor_sessions(doctor_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(ADMIN)):
    u = _doctor(db, doctor_id)
    n = revoke_all_sessions(db, u.id, reason="admin_revoked")
    record_event(db, "auth.session_revoke", actor=cu.user, target_user_id=u.id, details={"revoked": n}, commit=False)
    db.commit()
    return {"revoked": n}


# --- settings -----------------------------------------------------------------------------

def _settings_payload(cfg) -> dict:
    keys = ["activation_token_hours", "reset_token_hours", "max_failed_logins", "lockout_minutes",
            "max_activation_attempts", "remember_me_days", "allow_forgot_password_requests", "allow_emergency_access",
            "emergency_access_max_minutes"]
    s = get_settings()
    return {**{k: getattr(cfg, k) for k in keys}, "updated_at": iso(cfg.updated_at),
            "environment": {"session_ttl_minutes": s.session_ttl_minutes, "session_idle_timeout_minutes":
                            s.session_idle_timeout_minutes, "password_min_length": s.password_min_length,
                            "secure_cookies": s.session_cookie_secure,
                            "note": "Environment values are set in backend .env and require a restart."}}


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), cu: CurrentUser = Depends(require("admin.settings"))):
    cfg = get_auth_settings(db)
    db.commit()
    return _settings_payload(cfg)


@router.patch("/settings")
def update_settings(body: AuthSettingsUpdate, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("admin.settings"))):
    cfg = get_auth_settings(db)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changes.items():
        setattr(cfg, k, v)
    cfg.updated_by = cu.user.id
    record_event(db, "admin.settings_update", actor=cu.user, ip_hash=cu.ip_hash,
                 details={k: v for k, v in changes.items()}, commit=False)
    db.commit()
    return _settings_payload(cfg)


# --- staff users (non-doctor roles) -----------------------------------------------------------

@router.get("/users")
def list_users(p: Pagination = Depends(pagination), role: str | None = None, db: Session = Depends(get_db),
               cu: CurrentUser = Depends(require("admin.users"))):
    q = select(User).where(User.deleted_at.is_(None))
    if role:
        q = q.where(User.role == role)
    return paginate(db, q.order_by(User.created_at), p, lambda rows: [user_payload(u) for u in rows])


@router.post("/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("admin.users"))):
    email = body.email.lower()
    if body.role == Role.doctor:
        raise ValidationFailed("Doctors are provisioned via /admin/doctors and activate their own accounts",
                               details=[{"field": "role", "issue": "Use the doctor provisioning workflow"}])
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise Conflict("A user with this email already exists", code="USER_EXISTS")
    validate_password_strength(body.password, email)
    u = User(email=email, display_name=body.display_name, role=body.role, department=body.department,
             password_hash=hash_password(body.password), must_change_password=True, activated_at=utcnow())
    db.add(u)
    db.flush()
    record_event(db, "admin.user_create", actor=cu.user, target_user_id=u.id, resource_type="user", resource_id=u.id,
                 details={"role": body.role}, commit=False)
    db.commit()
    return {"user": user_payload(u)}


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("admin.users"))):
    u = db.get(User, user_id)
    if u is None or u.deleted_at is not None:
        raise NotFound("User not found", code="USER_NOT_FOUND")
    changes = body.model_dump(exclude_unset=True)
    if u.id == cu.user.id and ("role" in changes or changes.get("status") == "disabled"):
        raise ValidationFailed("You cannot change your own role or disable your own account")
    if changes.get("status") == "disabled":
        changes["status"] = "deactivated"
    for k, v in changes.items():
        setattr(u, k, v)
    if changes.get("status") == "deactivated" or "role" in changes:
        revoke_all_sessions(db, u.id, reason="admin_change")
    record_event(db, "admin.permission_change" if "role" in changes else "admin.user_update", actor=cu.user,
                 target_user_id=u.id, resource_type="user", resource_id=u.id,
                 details={"fields": ",".join(sorted(changes)), "role": changes.get("role")}, commit=False)
    db.commit()
    return {"user": user_payload(u)}


@router.post("/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("admin.users"))):
    if db.get(User, user_id) is None:
        raise NotFound("User not found", code="USER_NOT_FOUND")
    n = revoke_all_sessions(db, user_id, reason="admin_revoked")
    record_event(db, "auth.session_revoke", actor=cu.user, target_user_id=user_id, details={"revoked": n}, commit=False)
    db.commit()
    return {"revoked": n}


# --- audit logs ---------------------------------------------------------------------------------

@router.get("/audit-logs")
def audit_logs(p: Pagination = Depends(pagination), action: str | None = Query(None, max_length=80),
               category: str | None = Query(None, pattern="^(auth|doctor|admin|clinical|case|document|report|finding)$"),
               outcome: str | None = Query(None, pattern="^(success|failure|denied)$"), case_id: str | None = None,
               actor_id: str | None = None, target_user_id: str | None = None, doctor_id: str | None = None,
               db: Session = Depends(get_db), cu: CurrentUser = Depends(require("admin.audit_logs"))):
    q = select(AuditLog).order_by(AuditLog.seq.desc())
    if action:
        q = q.where(AuditLog.action == action)
    if category:
        q = q.where(AuditLog.action.like(f"{category}.%"))
    if outcome:
        q = q.where(AuditLog.outcome == outcome)
    if case_id:
        q = q.where(AuditLog.case_id == case_id)
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if doctor_id:
        target_user_id = _doctor(db, doctor_id).id
    if target_user_id:
        q = q.where(AuditLog.target_user_id == target_user_id)
    record_event(db, "admin.audit_log_view", actor=cu.user)
    return paginate(db, q, p, lambda rows: [_event(r) for r in rows])


@router.get("/audit-logs/verify")
def audit_logs_verify(db: Session = Depends(get_db), cu: CurrentUser = Depends(require("admin.audit_logs"))):
    return verify_chain(db)
