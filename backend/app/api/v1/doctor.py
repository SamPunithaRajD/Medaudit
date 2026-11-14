"""Doctor private workspace. Every query is scoped to records the signed-in doctor owns or was explicitly
granted; identifiers in the URL or body are never trusted as proof of access."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require_role
from app.core.config import get_settings
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.models import (AuditFinding, AuditReport, Document, Patient, PatientAccess, PatientCase, ProcessingJob,
                        Review, User)
from app.models.enums import Role
from app.schemas.requests import DoctorProfileUpdate, PatientCreate, PatientShare, PatientUpdate, PreferencesUpdate
from app.security.access_control import (CurrentUser, accessible_cases_query, accessible_patients_query,
                                         get_patient_for_user, patient_access_level)
from app.security.audit_logging import record_event
from app.security.authentication import normalize_login_id
from app.security.rate_limit import limiter
from app.services.accounts import doctor_payload, preferences, preferences_payload
from app.services.case_service import create_case_from_upload, parse_metadata
from app.utils.ids import iso, utcnow

router = APIRouter(prefix="/api/v1/doctor", tags=["doctor workspace"])
DOCTOR = require_role(Role.doctor)
CLOSED = ("closed", "archived")


def _latest_job(db: Session, case_id: str) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.case_id == case_id).order_by(ProcessingJob.created_at.desc()))


def _case_row(db: Session, c: PatientCase, user: User) -> dict:
    job = _latest_job(db, c.id)
    return {"case_id": c.id, "patient_id": c.patient_id, "patient_ref": c.patient_ref, "department": c.department,
            "status": c.status, "review_status": c.review_status, "processing_state": c.processing_state,
            "document_status": job.status if job else None, "progress_percent": job.progress_percent if job else None,
            "access": "owner" if c.created_by == user.id else "assigned", "created_at": iso(c.created_at),
            "updated_at": iso(c.updated_at)}


def _patient_row(db: Session, p: Patient, user: User, level: str | None = None) -> dict:
    cases = db.scalars(select(PatientCase).where(PatientCase.patient_id == p.id, PatientCase.deleted_at.is_(None))
                       .order_by(PatientCase.updated_at.desc())).all()
    return {"id": p.id, "patient_ref": p.patient_ref, "display_name": p.display_name, "age_years": p.age_years,
            "gender": p.gender, "department": p.department, "access": level or patient_access_level(db, user, p),
            "case_count": len(cases), "latest_case_status": cases[0].status if cases else None,
            "latest_case_id": cases[0].id if cases else None, "created_at": iso(p.created_at), "updated_at": iso(p.updated_at)}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    u = cu.user
    case_ids = select(accessible_cases_query(u).subquery().c.id)
    patients_q = accessible_patients_query(u)
    total_patients = db.scalar(select(func.count()).select_from(patients_q.subquery())) or 0
    active_cases = db.scalar(select(func.count()).select_from(PatientCase).where(
        PatientCase.id.in_(case_ids), PatientCase.status.not_in(CLOSED))) or 0
    docs = db.scalars(select(Document).where(Document.case_id.in_(case_ids), Document.deleted_at.is_(None))
                      .order_by(Document.uploaded_at.desc()).limit(5)).all()
    recent_patients = db.scalars(patients_q.order_by(Patient.updated_at.desc()).limit(5)).all()
    pending_findings = db.scalar(select(func.count()).select_from(AuditFinding).where(
        AuditFinding.case_id.in_(case_ids), AuditFinding.review_status == "pending", AuditFinding.superseded.is_(False))) or 0
    pending_reviews = db.scalar(select(func.count()).select_from(Review).where(
        Review.case_id.in_(case_ids), Review.assignee_id == u.id, Review.status.in_(["pending", "under_review"]))) or 0
    reports = db.scalars(select(AuditReport).where(AuditReport.case_id.in_(case_ids))
                         .order_by(AuditReport.generated_at.desc()).limit(5)).all()
    return {
        "doctor": doctor_payload(db, u),
        "welcome_name": u.display_name,
        "total_patients": total_patients,
        "active_audit_cases": active_cases,
        "pending_finding_reviews": pending_findings,
        "pending_reviews_assigned": pending_reviews,
        "recent_documents": [{"document_id": d.id, "case_id": d.case_id, "filename": d.filename, "status": d.status,
                              "page_count": d.page_count, "uploaded_at": iso(d.uploaded_at)} for d in docs],
        "recent_patient_activity": [_patient_row(db, p, u) for p in recent_patients],
        "recent_reports": [{"report_id": r.id, "case_id": r.case_id, "version": r.version,
                            "generated_at": iso(r.generated_at), "review_status": r.review_status} for r in reports],
    }


@router.get("/profile")
def get_profile(db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    return {"profile": doctor_payload(db, cu.user, detailed=True),
            "editable_fields": ["phone", "contact_info"],
            "read_only_fields": ["doctor_id", "full_name", "email", "department", "designation", "role"]}


@router.patch("/profile")
def update_profile(body: DoctorProfileUpdate, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    prof = cu.user.doctor_profile
    if prof is None:
        raise NotFound("Doctor profile not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(prof, k, v)
    record_event(db, "doctor.profile_update", actor=cu.user, target_user_id=cu.user.id,
                 details={"fields": ",".join(sorted(changes))}, commit=False)
    db.commit()
    return {"profile": doctor_payload(db, cu.user, detailed=True)}


@router.get("/preferences")
def get_preferences(db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    p = preferences(db, cu.user.id)
    db.commit()
    return {"preferences": preferences_payload(p)}


@router.patch("/preferences")
def update_preferences(body: PreferencesUpdate, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    p = preferences(db, cu.user.id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for k in ("notification_preferences", "dashboard_preferences"):
        if k in changes:
            changes[k] = {**(getattr(p, k) or {}), **changes[k]}
    for k, v in changes.items():
        setattr(p, k, v)
    db.commit()
    return {"preferences": preferences_payload(p)}


# --- patients -------------------------------------------------------------------------------

@router.get("/patients")
def list_patients(p: Pagination = Depends(pagination), q: str | None = Query(None, max_length=100),
                  case_status: str | None = None, scope: str = Query("all", pattern="^(all|owned|shared)$"),
                  db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    query = accessible_patients_query(cu.user)
    if scope == "owned":
        query = query.where(Patient.owner_user_id == cu.user.id)
    elif scope == "shared":
        query = query.where(Patient.owner_user_id != cu.user.id)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(Patient.patient_ref).like(like), func.lower(Patient.department).like(like),
                                func.lower(Patient.id).like(like)))
    if case_status:
        query = query.where(exists().where(PatientCase.patient_id == Patient.id, PatientCase.status == case_status))
    query = query.order_by(Patient.updated_at.desc(), Patient.id)
    return paginate(db, query, p, lambda rows: [_patient_row(db, x, cu.user) for x in rows])


@router.post("/patients", status_code=201)
def create_patient(body: PatientCreate, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    patient = Patient(owner_user_id=cu.user.id, patient_ref=body.patient_ref.strip(), display_name=body.display_name,
                      age_years=body.age_years, gender=body.gender, department=body.department or cu.user.department,
                      notes=body.notes)
    db.add(patient)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("You already have a patient with this reference", code="PATIENT_EXISTS",
                       details=[{"field": "patient_ref", "issue": "Duplicate reference"}]) from exc
    record_event(db, "patient.create", actor=cu.user, resource_type="patient", resource_id=patient.id, commit=False)
    db.commit()
    return {"patient": _patient_row(db, patient, cu.user, "owner")}


@router.get("/patients/{patient_id}")
def patient_detail(patient_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    patient = get_patient_for_user(db, cu.user, patient_id)
    level = patient_access_level(db, cu.user, patient)
    cases = db.scalars(select(PatientCase).where(PatientCase.patient_id == patient.id, PatientCase.deleted_at.is_(None))
                       .order_by(PatientCase.created_at.desc())).all()
    shares = []
    if level == "owner":
        rows = db.execute(select(PatientAccess, User).join(User, User.id == PatientAccess.user_id)
                          .where(PatientAccess.patient_id == patient.id, PatientAccess.revoked_at.is_(None))).all()
        shares = [{"doctor_id": usr.login_id, "full_name": usr.display_name, "access_level": acc.access_level,
                   "granted_at": iso(acc.created_at)} for acc, usr in rows]
    record_event(db, "patient.view", actor=cu.user, resource_type="patient", resource_id=patient.id)
    return {"patient": {**_patient_row(db, patient, cu.user, level), "notes": patient.notes},
            "cases": [_case_row(db, c, cu.user) for c in cases], "shared_with": shares}


@router.patch("/patients/{patient_id}")
def update_patient(patient_id: str, body: PatientUpdate, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    patient = get_patient_for_user(db, cu.user, patient_id, need="contribute")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(patient, k, v)
    patient.updated_at = utcnow()
    record_event(db, "patient.update", actor=cu.user, resource_type="patient", resource_id=patient.id,
                 details={"fields": ",".join(sorted(changes))}, commit=False)
    db.commit()
    return {"patient": _patient_row(db, patient, cu.user)}


@router.post("/patients/{patient_id}/documents", status_code=202)
def upload_patient_document(patient_id: str, file: UploadFile = File(...), metadata: str | None = Form(None),
                            idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=100),
                            db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    """Upload a case sheet for one of your patients; creates a new audit case linked to the patient."""
    patient = get_patient_for_user(db, cu.user, patient_id, need="contribute")
    limiter.hit(f"upload:{cu.user.id}", get_settings().sensitive_rate_limit_per_minute)
    case, doc, job = create_case_from_upload(db, cu.user, filename=file.filename, stream=file.file,
                                             department=patient.department, patient_ref=patient.patient_ref,
                                             metadata=parse_metadata(metadata), idempotency_key=idempotency_key,
                                             patient=patient)
    record_event(db, "case.upload", actor=cu.user, resource_type="document", resource_id=doc.id, case_id=case.id,
                 ip_hash=cu.ip_hash, details={"patient_id": patient.id, "pages": doc.page_count})
    return {"case_id": case.id, "document_id": doc.id, "job_id": job.id, "status": job.status, "patient_id": patient.id,
            "created_at": iso(case.created_at), "status_url": f"/api/v1/cases/{case.id}/status", "estimated_seconds": None}


@router.get("/patients/{patient_id}/cases")
def patient_cases(patient_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    patient = get_patient_for_user(db, cu.user, patient_id)
    cases = db.scalars(select(PatientCase).where(PatientCase.patient_id == patient.id, PatientCase.deleted_at.is_(None))
                       .order_by(PatientCase.created_at.desc())).all()
    return {"items": [_case_row(db, c, cu.user) for c in cases]}


@router.post("/patients/{patient_id}/share", status_code=201)
def share_patient(patient_id: str, body: PatientShare, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    patient = get_patient_for_user(db, cu.user, patient_id, need="owner")
    target = db.scalar(select(User).where(User.login_id == normalize_login_id(body.doctor_id), User.role == Role.doctor,
                                          User.status == "active"))
    if target is None:
        raise ValidationFailed("No active doctor with that ID", details=[{"field": "doctor_id", "issue": "Not found"}])
    if target.id == cu.user.id:
        raise ValidationFailed("You already own this patient")
    row = db.scalar(select(PatientAccess).where(PatientAccess.patient_id == patient.id, PatientAccess.user_id == target.id))
    if row is None:
        row = PatientAccess(patient_id=patient.id, user_id=target.id, granted_by=cu.user.id)
        db.add(row)
    row.access_level, row.revoked_at = body.access_level, None
    record_event(db, "patient.share", actor=cu.user, target_user_id=target.id, resource_type="patient",
                 resource_id=patient.id, details={"access_level": body.access_level}, commit=False)
    db.commit()
    return {"shared_with": {"doctor_id": target.login_id, "full_name": target.display_name, "access_level": row.access_level}}


@router.delete("/patients/{patient_id}/share/{doctor_id}")
def unshare_patient(patient_id: str, doctor_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    patient = get_patient_for_user(db, cu.user, patient_id, need="owner")
    target = db.scalar(select(User).where(User.login_id == normalize_login_id(doctor_id)))
    row = db.scalar(select(PatientAccess).where(PatientAccess.patient_id == patient.id,
                                                PatientAccess.user_id == (target.id if target else "")))
    if row is None or row.revoked_at is not None:
        raise NotFound("Share not found")
    row.revoked_at = utcnow()
    record_event(db, "patient.unshare", actor=cu.user, target_user_id=target.id, resource_type="patient",
                 resource_id=patient.id, commit=False)
    db.commit()
    return {"status": "revoked"}


# --- cases --------------------------------------------------------------------------------------

@router.get("/cases")
def my_cases(p: Pagination = Depends(pagination), status: str | None = None, review_status: str | None = None,
             q: str | None = Query(None, max_length=100), db: Session = Depends(get_db), cu: CurrentUser = Depends(DOCTOR)):
    query = accessible_cases_query(cu.user)
    if status:
        query = query.where(PatientCase.status == status)
    if review_status:
        query = query.where(PatientCase.review_status == review_status)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(PatientCase.id).like(like), func.lower(PatientCase.patient_ref).like(like)))
    query = query.order_by(PatientCase.updated_at.desc(), PatientCase.id)
    return paginate(db, query, p, lambda rows: [_case_row(db, c, cu.user) for c in rows])

