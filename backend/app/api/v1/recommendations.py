"""Recommendations and corrective actions. In the contract, POST/PATCH /actions return {recommendation}:
a corrective action is a recommendation that is being tracked (tracked_as_action=true), with an
append-only status history in corrective_action_updates."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.audit_engine.recommendations.engine import due_date_for
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.models import AuditFinding, Comment, CorrectiveActionUpdate, Recommendation, User
from app.schemas.requests import RecommendationCreate, RecommendationUpdate
from app.schemas.responses import RecommendationEnvelope
from app.security.access_control import CurrentUser, accessible_cases_query, get_case_for_user
from app.security.audit_logging import record_event
from app.services import serializers as S
from app.utils.ids import ensure_utc, iso, utcnow

router = APIRouter(prefix="/api/v1", tags=["recommendations"])

TRANSITIONS = {"open": {"in_progress", "complete", "cancelled"}, "in_progress": {"complete", "cancelled", "open"},
               "complete": {"reopened"}, "reopened": {"in_progress", "complete", "cancelled"}, "cancelled": {"reopened"}}


def _create(db: Session, cu: CurrentUser, body: RecommendationCreate, tracked: bool) -> Recommendation:
    case = get_case_for_user(db, cu.user, body.case_id, "recommendation.manage")
    if body.finding_id:
        f = db.get(AuditFinding, body.finding_id)
        if f is None or f.case_id != case.id:
            raise ValidationFailed("finding_id must belong to the case", details=[{"field": "finding_id", "issue": "Invalid"}])
    if body.owner_id and db.get(User, body.owner_id) is None:
        raise ValidationFailed("Unknown owner", details=[{"field": "owner_id", "issue": "Not found"}])
    rec = Recommendation(case_id=case.id, finding_id=body.finding_id, category=body.category, description=body.description,
                         priority=body.priority, suggested_owner=body.suggested_owner, owner_id=body.owner_id,
                         due_date=body.due_date or due_date_for(body.priority), status="open", source="human",
                         tracked_as_action=tracked, created_by=cu.user.id,
                         supporting_evidence=[{"finding_id": body.finding_id}] if body.finding_id else [])
    db.add(rec)
    db.flush()
    if tracked:
        db.add(CorrectiveActionUpdate(recommendation_id=rec.id, actor_id=cu.user.id, from_status=None, to_status="open",
                                      note="Corrective action created"))
        if case.status == "approved":
            case.status = "corrective_action_tracking"
    record_event(db, "recommendation.create", actor=cu.user, resource_type="recommendation", resource_id=rec.id,
                 case_id=case.id, details={"tracked": tracked, "priority": body.priority}, commit=False)
    db.commit()
    return rec


def _update(db: Session, cu: CurrentUser, rec_id: str, body: RecommendationUpdate, as_action: bool) -> Recommendation:
    rec = db.get(Recommendation, rec_id)
    if rec is None:
        raise NotFound("Recommendation not found", code="RECOMMENDATION_NOT_FOUND")
    get_case_for_user(db, cu.user, rec.case_id, "recommendation.manage")
    changes = body.model_dump(exclude_unset=True)
    expected = changes.pop("expected_updated_at", None)
    if expected and ensure_utc(expected).replace(microsecond=0) != ensure_utc(rec.updated_at).replace(microsecond=0):
        raise Conflict("This item changed since you loaded it; refresh and retry", code="REVIEW_CONFLICT")
    comment = changes.pop("reviewer_comment", None)
    new_status = changes.pop("status", None)
    if new_status and new_status != rec.status:
        if new_status not in TRANSITIONS.get(rec.status, set()):
            raise ValidationFailed(f"Cannot change status from {rec.status} to {new_status}",
                                   details=[{"field": "status", "issue": "Invalid transition"}])
        db.add(CorrectiveActionUpdate(recommendation_id=rec.id, actor_id=cu.user.id, from_status=rec.status,
                                      to_status=new_status, note=comment))
        rec.status = new_status
        rec.completed_at = utcnow() if new_status == "complete" else None
    if "owner_id" in changes and changes["owner_id"] and db.get(User, changes["owner_id"]) is None:
        raise ValidationFailed("Unknown owner", details=[{"field": "owner_id", "issue": "Not found"}])
    for k, v in changes.items():
        setattr(rec, k, v)
    if as_action:
        rec.tracked_as_action = True
    if comment:
        db.add(Comment(case_id=rec.case_id, target_type="recommendation", target_id=rec.id, author_id=cu.user.id, body=comment))
    rec.updated_at = utcnow()
    record_event(db, "recommendation.update", actor=cu.user, resource_type="recommendation", resource_id=rec.id,
                 case_id=rec.case_id, details={"status": new_status, "fields": ",".join(sorted(changes))}, commit=False)
    db.commit()
    return rec


@router.get("/cases/{case_id}/recommendations")
def case_recommendations(case_id: str, p: Pagination = Depends(pagination), status: str | None = None,
                         category: str | None = None, owner_id: str | None = None, db: Session = Depends(get_db),
                         cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    q = select(Recommendation).where(Recommendation.case_id == case.id)
    for col, val in ((Recommendation.status, status), (Recommendation.category, category), (Recommendation.owner_id, owner_id)):
        if val:
            q = q.where(col == val)
    return paginate(db, q.order_by(Recommendation.created_at), p, lambda rows: S.recommendations(db, rows))


@router.post("/recommendations", status_code=201, response_model=RecommendationEnvelope)
def create_recommendation(body: RecommendationCreate, db: Session = Depends(get_db),
                          cu: CurrentUser = Depends(require("recommendation.manage"))):
    return {"recommendation": S.recommendations(db, [_create(db, cu, body, tracked=False)])[0]}


@router.patch("/recommendations/{recommendation_id}", response_model=RecommendationEnvelope)
def update_recommendation(recommendation_id: str, body: RecommendationUpdate, db: Session = Depends(get_db),
                          cu: CurrentUser = Depends(require("recommendation.manage"))):
    return {"recommendation": S.recommendations(db, [_update(db, cu, recommendation_id, body, as_action=False)])[0]}


@router.get("/actions")
def list_actions(p: Pagination = Depends(pagination), status: str | None = None, owner_id: str | None = None,
                 case_id: str | None = None, db: Session = Depends(get_db),
                 cu: CurrentUser = Depends(require("recommendation.manage"))):
    case_ids = select(accessible_cases_query(cu.user).subquery().c.id)
    q = select(Recommendation).where(Recommendation.tracked_as_action.is_(True), Recommendation.case_id.in_(case_ids))
    for col, val in ((Recommendation.status, status), (Recommendation.owner_id, owner_id), (Recommendation.case_id, case_id)):
        if val:
            q = q.where(col == val)
    return paginate(db, q.order_by(Recommendation.due_date.is_(None), Recommendation.due_date), p,
                    lambda rows: S.recommendations(db, rows))


@router.post("/actions", status_code=201, response_model=RecommendationEnvelope)
def create_action(body: RecommendationCreate, db: Session = Depends(get_db),
                  cu: CurrentUser = Depends(require("recommendation.manage"))):
    return {"recommendation": S.recommendations(db, [_create(db, cu, body, tracked=True)])[0]}


@router.patch("/actions/{action_id}", response_model=RecommendationEnvelope)
def update_action(action_id: str, body: RecommendationUpdate, db: Session = Depends(get_db),
                  cu: CurrentUser = Depends(require("recommendation.manage"))):
    return {"recommendation": S.recommendations(db, [_update(db, cu, action_id, body, as_action=True)])[0]}


@router.get("/actions/{action_id}/history")
def action_history(action_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("recommendation.manage"))):
    rec = db.get(Recommendation, action_id)
    if rec is None:
        raise NotFound("Action not found")
    get_case_for_user(db, cu.user, rec.case_id)
    rows = db.scalars(select(CorrectiveActionUpdate).where(CorrectiveActionUpdate.recommendation_id == rec.id)
                      .order_by(CorrectiveActionUpdate.created_at)).all()
    return {"items": [{"id": r.id, "actor_id": r.actor_id, "from_status": r.from_status, "to_status": r.to_status,
                       "note": r.note, "created_at": iso(r.created_at)} for r in rows]}
