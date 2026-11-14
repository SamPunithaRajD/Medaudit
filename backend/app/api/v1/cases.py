from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.core.config import get_settings
from app.core.exceptions import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import (AgentExecution, AuditFinding, CaseAssignment, Document, PatientCase, ProcessingJob,
                        Recommendation, User)
from app.models.enums import Role
from app.schemas.requests import AssignmentRequest, CaseUpdate, EmergencyAccessRequest
from app.schemas.responses import CaseDetail, CaseStatusResponse, UploadResponse
from app.security.access_control import (CurrentUser, accessible_cases_query, get_case_for_user, get_patient_for_user,
                                         has_permission)
from app.security.authentication import get_auth_settings
from app.security.audit_logging import record_event
from app.security.rate_limit import limiter
from app.services import serializers as S
from app.services.case_service import create_case_from_upload, parse_metadata, requeue_case
from app.utils.ids import ensure_utc, iso, utcnow
from app.workers.queue import queue

router = APIRouter(prefix="/api/v1", tags=["cases"])

COMMITTEE_ONLY_CASE_STATUS = {"approved", "corrective_action_tracking", "closed"}
COMMITTEE_ONLY_REVIEW_STATUS = {"approved"}
SORTS = {"created_at": PatientCase.created_at, "updated_at": PatientCase.updated_at,
         "admitted_at": PatientCase.admitted_at, "outcome_at": PatientCase.outcome_at, "status": PatientCase.status}


def _job_payload(job: ProcessingJob | None, case_id: str) -> dict:
    if job is None:
        return {"case_id": case_id, "job_id": None, "status": None, "progress_percent": None, "stage": None,
                "stages": [], "updated_at": None, "error": None}
    return {
        "case_id": case_id, "job_id": job.id, "status": job.status, "progress_percent": job.progress_percent,
        "stage": job.current_stage,
        "stages": [{"key": s.key, "label": s.label, "status": s.status, "started_at": iso(s.started_at),
                    "completed_at": iso(s.completed_at), "detail": s.detail or {}} for s in job.stages],
        "progress": {"current_stage": job.current_stage, "completed_pages": job.completed_pages,
                     "failed_pages": job.failed_pages, "total_pages": job.total_pages,
                     "percentage": job.progress_percent},
        "attempts": job.attempts, "max_attempts": job.max_attempts, "warnings": job.warnings or [],
        "updated_at": iso(job.updated_at),
        "error": {"code": job.error_code, "message": job.error_message, "retryable": job.retryable} if job.error_code else None,
    }


def _latest_job(db: Session, case_id: str) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.case_id == case_id).order_by(ProcessingJob.created_at.desc()))


@router.post("/cases/upload", status_code=202, response_model=UploadResponse)
def upload_case(request: Request, file: UploadFile = File(...), department: str | None = Form(None, max_length=120),
                patient_ref: str | None = Form(None, max_length=120), metadata: str | None = Form(None),
                patient_id: str | None = Form(None, max_length=40),
                idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=100),
                db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.upload"))):
    limiter.hit(f"upload:{cu.user.id}", get_settings().sensitive_rate_limit_per_minute)
    meta = parse_metadata(metadata)
    patient = get_patient_for_user(db, cu.user, patient_id, need="contribute") if patient_id else None
    try:
        case, doc, job = create_case_from_upload(db, cu.user, filename=file.filename, stream=file.file,
                                                 department=department or (patient.department if patient else None),
                                                 patient_ref=patient_ref or (patient.patient_ref if patient else None),
                                                 metadata=meta, idempotency_key=idempotency_key, patient=patient)
    except Exception as exc:
        db.rollback()
        record_event(db, "case.upload", actor=cu.user, outcome="failure", ip_hash=cu.ip_hash,
                     details={"code": getattr(exc, "code", type(exc).__name__)})
        raise
    record_event(db, "case.upload", actor=cu.user, resource_type="document", resource_id=doc.id, case_id=case.id,
                 ip_hash=cu.ip_hash, details={"pages": doc.page_count, "size_bytes": doc.size_bytes})
    return {"case_id": case.id, "document_id": doc.id, "job_id": job.id, "status": job.status,
            "created_at": iso(case.created_at), "status_url": f"/api/v1/cases/{case.id}/status",
            "estimated_seconds": None}


@router.get("/cases")
def list_cases(p: Pagination = Depends(pagination), status: str | None = None, department: str | None = None,
               review_status: str | None = None, from_: date | None = Query(None, alias="from"),
               to: date | None = None, q: str | None = Query(None, max_length=100), sort: str = "created_at:desc",
               db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    query = accessible_cases_query(cu.user)
    if status:
        query = query.where(PatientCase.status == status)
    if review_status:
        query = query.where(PatientCase.review_status == review_status)
    if department:
        query = query.where(func.lower(PatientCase.department) == department.lower())
    if from_:
        query = query.where(PatientCase.created_at >= datetime.combine(from_, time.min, tzinfo=timezone.utc))
    if to:
        query = query.where(PatientCase.created_at <= datetime.combine(to, time.max, tzinfo=timezone.utc))
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(PatientCase.id).like(like), func.lower(PatientCase.patient_ref).like(like),
                                func.lower(PatientCase.department).like(like)))
    field, _, direction = sort.partition(":")
    col = SORTS.get(field)
    if col is None:
        raise ValidationFailed("Unsupported sort field", details=[{"field": "sort", "issue": f"Use one of {sorted(SORTS)}"}])
    query = query.order_by(col.asc() if direction == "asc" else col.desc(), PatientCase.id)
    return paginate(db, query, p, lambda rows: [S.case(db, c) for c in rows])


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    record_event(db, "case.view", actor=cu.user, resource_type="case", resource_id=case.id, case_id=case.id,
                 ip_hash=cu.ip_hash)
    count = lambda model, *cond: db.scalar(select(func.count()).select_from(model).where(model.case_id == case.id, *cond))  # noqa: E731
    return {"case": S.case(db, case),
            "document_count": count(Document, Document.deleted_at.is_(None)),
            "finding_count": count(AuditFinding, AuditFinding.superseded.is_(False)),
            "recommendation_count": count(Recommendation),
            "latest_job": _job_payload(_latest_job(db, case.id), case.id)}


@router.patch("/cases/{case_id}")
def update_case(case_id: str, body: CaseUpdate, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("case.update"))):
    case = get_case_for_user(db, cu.user, case_id, "case.update")
    changes = body.model_dump(exclude_unset=True)
    expected = changes.pop("expected_updated_at", None)
    if expected and ensure_utc(expected).replace(microsecond=0) != ensure_utc(case.updated_at).replace(microsecond=0):
        raise Conflict("The case was modified by someone else; refresh and retry", code="CASE_CONFLICT")
    committee = cu.user.role in (Role.committee_member, Role.admin)
    if changes.get("status") in COMMITTEE_ONLY_CASE_STATUS and not committee:
        raise Forbidden("Only committee members or administrators can set this status", code="COMMITTEE_ONLY")
    if changes.get("review_status") in COMMITTEE_ONLY_REVIEW_STATUS and not committee:
        raise Forbidden("Only committee members or administrators can approve a case", code="COMMITTEE_ONLY")
    prov = dict(case.field_provenance or {})
    for k, v in changes.items():
        setattr(case, k, v)
        if k in ("patient_ref", "department", "ward", "cause_as_recorded", "death_location"):
            prov[k] = {"source": "human", "user_id": cu.user.id, "at": iso(utcnow())}
    case.field_provenance = prov
    if changes.get("review_status") == "under_review" and case.review_started_at is None:
        case.review_started_at = utcnow()
    if changes.get("review_status") == "approved":
        case.review_completed_at = utcnow()
        case.processing_state = "completed"
    record_event(db, "case.update", actor=cu.user, resource_type="case", resource_id=case.id, case_id=case.id,
                 ip_hash=cu.ip_hash, details={"fields": ",".join(sorted(changes)), "status": changes.get("status"),
                                              "review_status": changes.get("review_status")}, commit=False)
    db.commit()
    return {"case": S.case(db, case)}


def _case_exists(db: Session, case_id: str) -> PatientCase:
    """Existence check for administrative operations that must not expose clinical content."""
    case = db.get(PatientCase, case_id)
    if case is None or case.deleted_at is not None:
        raise NotFound("Case not found", code="CASE_NOT_FOUND")
    return case


def _assignments(db: Session, case_id: str) -> list[dict]:
    rows = db.execute(select(CaseAssignment, User).join(User, User.id == CaseAssignment.user_id)
                      .where(CaseAssignment.case_id == case_id)).all()
    return [{"user_id": u.id, "login_id": u.login_id, "display_name": u.display_name, "role": u.role,
             "assignment_role": a.assignment_role, "expires_at": iso(a.expires_at)} for a, u in rows]


@router.post("/cases/{case_id}/assignments", status_code=201)
def assign(case_id: str, body: AssignmentRequest, db: Session = Depends(get_db),
           cu: CurrentUser = Depends(require("case.assign"))):
    """Grant a user access to a case. Administrators manage access without gaining clinical access themselves."""
    case = _case_exists(db, case_id)
    user = db.get(User, body.user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        raise NotFound("User not found", code="USER_NOT_FOUND")
    existing = db.scalar(select(CaseAssignment).where(CaseAssignment.case_id == case.id, CaseAssignment.user_id == user.id))
    if existing:
        existing.assignment_role, existing.expires_at = body.assignment_role, None
    else:
        db.add(CaseAssignment(case_id=case.id, user_id=user.id, assignment_role=body.assignment_role,
                              granted_by=cu.user.id))
    record_event(db, "case.assign", actor=cu.user, target_user_id=user.id, resource_type="user", resource_id=user.id,
                 case_id=case.id, commit=False)
    db.commit()
    return {"case_id": case.id, "assignments": _assignments(db, case.id)}


@router.delete("/cases/{case_id}/assignments/{user_id}")
def unassign(case_id: str, user_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.assign"))):
    case = _case_exists(db, case_id)
    row = db.scalar(select(CaseAssignment).where(CaseAssignment.case_id == case.id, CaseAssignment.user_id == user_id))
    if row is None:
        raise NotFound("Assignment not found")
    if row.assignment_role == "uploader":
        raise Conflict("The uploading clinician's ownership cannot be removed", code="OWNER_ASSIGNMENT")
    db.delete(row)
    record_event(db, "case.unassign", actor=cu.user, target_user_id=user_id, resource_type="user", resource_id=user_id,
                 case_id=case.id, commit=False)
    db.commit()
    return {"case_id": case.id, "assignments": _assignments(db, case.id)}


@router.post("/cases/{case_id}/emergency-access", status_code=201)
def emergency_access(case_id: str, body: EmergencyAccessRequest, db: Session = Depends(get_db),
                     cu: CurrentUser = Depends(require("clinical.emergency_access"))):
    """Exceptional, time-limited access to a case for an authorised user, with a recorded justification.
    Every grant is written to the security audit log and shown on the administrator dashboard."""
    cfg = get_auth_settings(db)
    if not cfg.allow_emergency_access:
        raise Forbidden("Emergency access is disabled by hospital policy", code="EMERGENCY_ACCESS_DISABLED")
    case = _case_exists(db, case_id)
    minutes = min(body.duration_minutes, cfg.emergency_access_max_minutes)
    expires = utcnow() + timedelta(minutes=minutes)
    row = db.scalar(select(CaseAssignment).where(CaseAssignment.case_id == case.id, CaseAssignment.user_id == cu.user.id))
    if row is not None and row.expires_at is None:
        raise Conflict("You already have standing access to this case", code="ALREADY_ASSIGNED")
    if row is None:
        row = CaseAssignment(case_id=case.id, user_id=cu.user.id, assignment_role="emergency", granted_by=cu.user.id)
        db.add(row)
    row.expires_at, row.reason = expires, body.reason
    record_event(db, "clinical.emergency_access", actor=cu.user, target_user_id=cu.user.id, resource_type="case",
                 resource_id=case.id, case_id=case.id, ip_hash=cu.ip_hash,
                 details={"reason": body.reason, "minutes": minutes}, commit=False)
    db.commit()
    return {"case_id": case.id, "expires_at": iso(expires), "duration_minutes": minutes,
            "notice": "Access is temporary and audited. It ends automatically at expiry."}


@router.get("/cases/{case_id}/status", response_model=CaseStatusResponse)
def case_status(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    return {**_job_payload(_latest_job(db, case.id), case.id), "case_status": case.status,
            "processing_state": case.processing_state, "review_status": case.review_status}


@router.post("/cases/{case_id}/process", status_code=202)
def process_case(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.process"))):
    case = get_case_for_user(db, cu.user, case_id, "case.process")
    job = requeue_case(db, cu.user, case)
    record_event(db, "case.process", actor=cu.user, resource_type="job", resource_id=job.id, case_id=case.id)
    return JSONResponse(status_code=202, content={**_job_payload(job, case.id),
                                                  "message": "Document processing has been queued"})


def _job_for_user(db: Session, cu: CurrentUser, job_id: str) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise NotFound("Job not found", code="JOB_NOT_FOUND")
    get_case_for_user(db, cu.user, job.case_id)  # 404 if inaccessible
    return job


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    job = _job_for_user(db, cu, job_id)
    return _job_payload(job, job.case_id)


@router.get("/jobs/{job_id}/agents")
def job_agents(job_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    job = _job_for_user(db, cu, job_id)
    rows = db.scalars(select(AgentExecution).where(AgentExecution.job_id == job.id).order_by(AgentExecution.created_at)).all()
    return {"job_id": job.id, "agents": [{
        "agent": a.agent_name, "stage_index": a.stage_index, "status": a.status, "attempts": a.attempts,
        "started_at": iso(a.started_at), "completed_at": iso(a.completed_at), "duration_ms": a.duration_ms,
        "llm_used": a.llm_used, "llm_model": a.llm_model, "summary": a.output_summary, "warnings": a.warnings,
        "error": a.error} for a in rows]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.process"))):
    job = _job_for_user(db, cu, job_id)
    if not has_permission(cu.user, "case.process"):
        raise Forbidden()
    ok = queue.cancel(db, job)
    record_event(db, "job.cancel", actor=cu.user, resource_type="job", resource_id=job.id, case_id=job.case_id,
                 outcome="success" if ok else "failure", commit=False)
    db.commit()
    if not ok:
        raise Conflict("Job already finished", code="JOB_TERMINAL")
    return _job_payload(job, job.case_id)
