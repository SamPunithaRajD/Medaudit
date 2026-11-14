from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_engine.agents.report_agent import build_report, store_report
from app.ai_engine.report_generation.pdf import render_pdf
from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.core.exceptions import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import AuditFinding, AuditReport, Comment, FindingReview
from app.models.enums import Role
from app.schemas.requests import FindingReviewRequest
from app.security.access_control import CurrentUser, get_case_for_user, has_permission
from app.security.audit_logging import record_event
from app.services import serializers as S
from app.utils.ids import ensure_utc, iso, utcnow

router = APIRouter(prefix="/api/v1", tags=["audit"])
SEVERITY_RANK = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "informational": 4}


def _latest_report(db: Session, case_id: str) -> AuditReport | None:
    return db.scalar(select(AuditReport).where(AuditReport.case_id == case_id).order_by(AuditReport.version.desc()))


@router.get("/cases/{case_id}/audit-report")
def audit_report(case_id: str, version: int | None = Query(None, ge=1), db: Session = Depends(get_db),
                 cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    q = select(AuditReport).where(AuditReport.case_id == case.id)
    row = db.scalar(q.where(AuditReport.version == version)) if version else _latest_report(db, case.id)
    if row is None:
        raise NotFound("No report has been generated for this case yet", code="REPORT_NOT_FOUND")
    report = json.loads(row.content)
    # Review state changes after generation; always show the live status next to the snapshot.
    report["review_status_at_generation"] = report.get("review_status")
    report["review_status"] = case.review_status
    report["review_status_live"] = case.review_status
    record_event(db, "report.view", actor=cu.user, resource_type="report", resource_id=row.id, case_id=case.id,
                 ip_hash=cu.ip_hash)
    return report


@router.post("/cases/{case_id}/audit-report/regenerate", status_code=201)
def regenerate_report(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.update"))):
    """Regenerate deterministically from current (reviewed) data — no LLM narrative is invoked here."""
    case = get_case_for_user(db, cu.user, case_id, "case.update")
    report, _, _ = build_report(db, case, llm=None)
    row = store_report(db, case, report, job_id=None, user_id=cu.user.id)
    record_event(db, "report.generate", actor=cu.user, resource_type="report", resource_id=row.id, case_id=case.id,
                 commit=False)
    db.commit()
    return {"report_id": row.id, "report_version": row.version, "generated_at": iso(row.generated_at)}


@router.get("/cases/{case_id}/audit-report/versions")
def report_versions(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    rows = db.scalars(select(AuditReport).where(AuditReport.case_id == case.id).order_by(AuditReport.version.desc())).all()
    return {"items": [{"report_id": r.id, "version": r.version, "generated_at": iso(r.generated_at),
                       "review_status": r.review_status, "sha256": r.content_sha256, "generated_by": r.generated_by}
                      for r in rows]}


@router.get("/cases/{case_id}/audit-report/export", response_class=Response,
            responses={200: {"content": {"application/pdf": {}, "application/json": {}}}})
def export_report(case_id: str, format: str = Query("pdf", pattern="^(pdf|json)$"), db: Session = Depends(get_db),
                  cu: CurrentUser = Depends(require("report.export"))):
    case = get_case_for_user(db, cu.user, case_id, "report.export")
    row = _latest_report(db, case.id)
    if row is None:
        raise NotFound("No report has been generated for this case yet", code="REPORT_NOT_FOUND")
    report = json.loads(row.content)
    report["review_status"] = case.review_status
    record_event(db, "report.export", actor=cu.user, resource_type="report", resource_id=row.id, case_id=case.id,
                 ip_hash=cu.ip_hash, details={"format": format, "version": row.version})
    headers = {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{case.id}-report-v{row.version}.{format}"'}
    if format == "json":
        return Response(json.dumps(report, indent=2), media_type="application/json", headers=headers)
    return Response(render_pdf(report), media_type="application/pdf", headers=headers)


@router.get("/cases/{case_id}/findings")
def case_findings(case_id: str, p: Pagination = Depends(pagination), category: str | None = None,
                  severity: str | None = None, review_status: str | None = None, verification_status: str | None = None,
                  include_superseded: bool = False, sort: str = Query("severity", pattern="^(severity|created_at|occurred_at)$"),
                  db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    q = select(AuditFinding).where(AuditFinding.case_id == case.id)
    if not include_superseded:
        q = q.where(AuditFinding.superseded.is_(False))
    for col, val in ((AuditFinding.category, category), (AuditFinding.severity, severity),
                     (AuditFinding.review_status, review_status), (AuditFinding.verification_status, verification_status)):
        if val:
            q = q.where(col == val)
    rows = db.scalars(q).all()
    key = {"severity": lambda f: (SEVERITY_RANK.get(f.severity, 9), f.created_at),
           "created_at": lambda f: f.created_at,
           "occurred_at": lambda f: (f.occurred_at is None, f.occurred_at or f.created_at)}[sort]
    rows = sorted(rows, key=key)
    total = len(rows)
    start = (p.page - 1) * p.page_size
    pages = (total + p.page_size - 1) // p.page_size
    return {"items": S.findings(db, rows[start:start + p.page_size]),
            "pagination": {"page": p.page, "page_size": p.page_size, "total_items": total, "total_pages": pages,
                           "next_page": p.page + 1 if p.page < pages else None,
                           "previous_page": p.page - 1 if p.page > 1 else None}}


def _finding(db: Session, cu: CurrentUser, finding_id: str, permission: str = "case.read") -> AuditFinding:
    f = db.get(AuditFinding, finding_id)
    if f is None:
        raise NotFound("Finding not found", code="FINDING_NOT_FOUND")
    get_case_for_user(db, cu.user, f.case_id, permission)
    return f


@router.get("/findings/{finding_id}")
def finding_detail(finding_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    f = _finding(db, cu, finding_id)
    history = db.scalars(select(FindingReview).where(FindingReview.finding_id == f.id).order_by(FindingReview.created_at)).all()
    return {"finding": S.findings(db, [f])[0],
            "review_history": [{"id": h.id, "actor_id": h.actor_id, "action": h.action, "review_status": h.review_status,
                                "comment": h.comment, "created_at": iso(h.created_at)} for h in history]}


ACTION_STATUS = {"accept": "approved", "reject": "rejected", "amend": "amended"}


@router.post("/findings/{finding_id}/review")
def review_finding(finding_id: str, body: FindingReviewRequest, db: Session = Depends(get_db),
                   cu: CurrentUser = Depends(require("finding.review"))):
    f = _finding(db, cu, finding_id, "finding.review")
    if body.expected_updated_at and ensure_utc(body.expected_updated_at).replace(microsecond=0) != ensure_utc(f.updated_at).replace(microsecond=0):
        raise Conflict("This finding changed since you loaded it; refresh and retry", code="REVIEW_CONFLICT")
    if body.action in ("accept", "reject", "amend") and not has_permission(cu.user, "finding.approve"):
        raise Forbidden("Your role can comment on findings but not accept, reject or amend them", code="FORBIDDEN")
    if body.action == "amend" and not (body.amended_title or body.amended_description):
        raise ValidationFailed("Amendment requires amended_title or amended_description",
                               details=[{"field": "amended_description", "issue": "Required for amend"}])
    if body.action == "comment" and not body.comment:
        raise ValidationFailed("Comment text is required", details=[{"field": "comment", "issue": "Required"}])
    previous = {"review_status": f.review_status, "title": f.amended_title or f.title,
                "description": f.amended_description or f.description}
    new_status = body.review_status or ACTION_STATUS.get(body.action, f.review_status)
    if body.action == "comment" and body.review_status is None:
        new_status = f.review_status if f.review_status != "pending" else "under_review"
    if new_status == "committee_review" and cu.user.role not in (Role.committee_member, Role.admin, Role.doctor):
        raise Forbidden()
    if body.action == "amend":
        f.amended_title = body.amended_title or f.amended_title
        f.amended_description = body.amended_description or f.amended_description
    f.review_status = new_status
    f.updated_at = utcnow()
    rev = FindingReview(finding_id=f.id, actor_id=cu.user.id, action=body.action, review_status=new_status,
                        comment=body.comment, previous=previous)
    db.add(rev)
    if body.comment:
        db.add(Comment(case_id=f.case_id, target_type="finding", target_id=f.id, author_id=cu.user.id, body=body.comment))
    case = get_case_for_user(db, cu.user, f.case_id)
    if case.review_status == "pending":
        case.review_status = "under_review"
        case.review_started_at = case.review_started_at or utcnow()
        if case.status == "pending_review":
            case.status = "under_review"
    db.flush()
    record_event(db, f"finding.{body.action}", actor=cu.user, resource_type="finding", resource_id=f.id, case_id=f.case_id,
                 ip_hash=cu.ip_hash, details={"review_status": new_status}, commit=False)
    db.commit()
    return {"finding": S.findings(db, [f])[0],
            "review": {"id": rev.id, "actor": {"user_id": cu.user.id, "display_name": cu.user.display_name},
                       "action": rev.action, "comment": rev.comment, "created_at": iso(rev.created_at)},
            "case_review_status": case.review_status}
