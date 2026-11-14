from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_db, require
from app.core.exceptions import Forbidden, ValidationFailed
from app.models import AuditFinding, PreventabilityAssessment
from app.schemas.requests import PreventabilityReviewRequest
from app.security.access_control import CurrentUser, get_case_for_user, has_permission
from app.security.audit_logging import record_event
from app.services import serializers as S
from app.utils.ids import utcnow

router = APIRouter(prefix="/api/v1/cases/{case_id}/preventability", tags=["preventability"])


def _latest(db: Session, case_id: str, kind: str):
    return db.scalar(select(PreventabilityAssessment).where(PreventabilityAssessment.case_id == case_id,
                                                            PreventabilityAssessment.assessment_type == kind)
                     .order_by(PreventabilityAssessment.created_at.desc()))


@router.get("")
def get_preventability(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    prelim = _latest(db, case.id, "preliminary")
    clinical = _latest(db, case.id, "clinical_review")
    committee = _latest(db, case.id, "committee_decision")
    findings = []
    if prelim:
        rows = db.scalars(select(AuditFinding).where(AuditFinding.id.in_(prelim.supporting_finding_ids or []))).all()
        findings = S.findings(db, rows)
    return {
        "case_id": case.id,
        "preliminary": S.assessment(prelim),
        "supporting_findings": findings,
        "clinical_reviewer": S.assessment(clinical),
        "committee_decision": S.assessment(committee),
        "clinical_reviewer_status": "completed" if clinical else "pending",
        "committee_status": case.review_status,
        "final_category": committee.category if committee else None,
        "notice": ("The preliminary output organises evidence for qualified reviewers. It cannot output 'preventable' or "
                   "'unavoidable'; those determinations are reserved for clinicians and the committee. It does not "
                   "establish negligence or legal causation."),
    }


@router.post("/review", status_code=201)
def review_preventability(case_id: str, body: PreventabilityReviewRequest, db: Session = Depends(get_db),
                          cu: CurrentUser = Depends(require("preventability.clinical_review"))):
    case = get_case_for_user(db, cu.user, case_id, "preventability.clinical_review")
    perm = f"preventability.{body.decision_type}"
    if not has_permission(cu.user, perm):
        record_event(db, "preventability.review", actor=cu.user, case_id=case.id, outcome="denied",
                     details={"decision_type": body.decision_type})
        raise Forbidden("Only committee members or administrators can record the committee decision", code="COMMITTEE_ONLY")
    valid_ids = set(db.scalars(select(AuditFinding.id).where(AuditFinding.case_id == case.id)).all())
    bad = [i for i in body.supporting_finding_ids if i not in valid_ids]
    if bad:
        raise ValidationFailed("Supporting findings must belong to this case",
                               details=[{"field": "supporting_finding_ids", "issue": f"Unknown: {', '.join(bad[:5])}"}])
    db.execute(update(PreventabilityAssessment).where(PreventabilityAssessment.case_id == case.id,
                                                      PreventabilityAssessment.assessment_type == body.decision_type)
               .values(is_current=False))
    pa = PreventabilityAssessment(case_id=case.id, assessment_type=body.decision_type, category=body.category,
                                  rationale=body.rationale, contributing_factors=body.contributing_factors,
                                  supporting_finding_ids=body.supporting_finding_ids, created_by=cu.user.id,
                                  framework_id="human_review", framework_version=None, is_current=True)
    db.add(pa)
    if body.decision_type == "clinical_review" and case.review_status in ("pending", "under_review"):
        case.review_status = "committee_review"
        case.status = "committee_review"
    elif body.decision_type == "committee_decision":
        case.review_status = "approved"
        case.status = "approved"
        case.review_completed_at = utcnow()
        case.processing_state = "completed"
    db.flush()
    record_event(db, f"preventability.{body.decision_type}", actor=cu.user, resource_type="preventability",
                 resource_id=pa.id, case_id=case.id, ip_hash=cu.ip_hash, details={"category": body.category}, commit=False)
    db.commit()
    return {"assessment": S.assessment(pa), "case_review_status": case.review_status}
