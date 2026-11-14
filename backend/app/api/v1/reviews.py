from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require, user_display
from app.core.exceptions import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import Comment, Review, User
from app.models.enums import Role
from app.schemas.requests import CommentCreate, ReviewCreate, ReviewDecision
from app.security.access_control import CurrentUser, accessible_cases_query, can_access_case, get_case_for_user
from app.security.audit_logging import record_event
from app.services.serializers import comments_for
from app.utils.ids import iso, utcnow

router = APIRouter(prefix="/api/v1/reviews", tags=["collaboration"])

DECISION_STATUS = {"approve": "approved", "reject": "rejected", "request_changes": "under_review",
                   "escalate_to_committee": "committee_review"}


def _payload(db: Session, r: Review) -> dict:
    return {"id": r.id, "case_id": r.case_id, "title": r.title, "review_type": r.review_type, "status": r.status,
            "created_by": user_display(db, r.created_by), "assignee": user_display(db, r.assignee_id),
            "decision": r.decision, "decision_rationale": r.decision_rationale, "decided_by": user_display(db, r.decided_by),
            "decided_at": iso(r.decided_at), "comments": comments_for(db, "review", [r.id]).get(r.id, []),
            "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}


def _review(db: Session, cu: CurrentUser, review_id: str) -> Review:
    r = db.get(Review, review_id)
    if r is None:
        raise NotFound("Review not found", code="REVIEW_NOT_FOUND")
    get_case_for_user(db, cu.user, r.case_id)
    return r


@router.get("")
def list_reviews(p: Pagination = Depends(pagination), case_id: str | None = None, status: str | None = None,
                 mine: bool = False, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("review.manage"))):
    case_ids = select(accessible_cases_query(cu.user).subquery().c.id)
    q = select(Review).where(Review.case_id.in_(case_ids))
    if case_id:
        q = q.where(Review.case_id == case_id)
    if status:
        q = q.where(Review.status == status)
    if mine:
        q = q.where(Review.assignee_id == cu.user.id)
    return paginate(db, q.order_by(Review.created_at.desc()), p, lambda rows: [_payload(db, r) for r in rows])


@router.post("", status_code=201)
def create_review(body: ReviewCreate, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("review.manage"))):
    case = get_case_for_user(db, cu.user, body.case_id, "review.manage")
    if body.assignee_id:
        assignee = db.get(User, body.assignee_id)
        if assignee is None or not can_access_case(db, assignee, case.id):
            raise ValidationFailed("Assignee must exist and be assigned to the case",
                                   details=[{"field": "assignee_id", "issue": "Invalid assignee"}])
    r = Review(case_id=case.id, title=body.title, review_type=body.review_type, created_by=cu.user.id,
               assignee_id=body.assignee_id, status="pending")
    db.add(r)
    db.flush()
    record_event(db, "review.create", actor=cu.user, resource_type="review", resource_id=r.id, case_id=case.id, commit=False)
    db.commit()
    return {"review": _payload(db, r)}


@router.get("/{review_id}")
def get_review(review_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("review.manage"))):
    return {"review": _payload(db, _review(db, cu, review_id))}


@router.post("/{review_id}/comments", status_code=201)
def add_comment(review_id: str, body: CommentCreate, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("review.manage"))):
    r = _review(db, cu, review_id)
    c = Comment(case_id=r.case_id, target_type="review", target_id=r.id, author_id=cu.user.id, body=body.body)
    db.add(c)
    if r.status == "pending":
        r.status = "under_review"
    db.flush()
    record_event(db, "review.comment", actor=cu.user, resource_type="review", resource_id=r.id, case_id=r.case_id, commit=False)
    db.commit()
    return {"comment": {"id": c.id, "author": {"user_id": cu.user.id, "display_name": cu.user.display_name},
                        "body": c.body, "created_at": iso(c.created_at)}, "review": _payload(db, r)}


@router.post("/{review_id}/decision")
def decide(review_id: str, body: ReviewDecision, db: Session = Depends(get_db),
           cu: CurrentUser = Depends(require("review.decide"))):
    r = _review(db, cu, review_id)
    if r.status in ("approved", "rejected"):
        raise Conflict("Review already decided", code="REVIEW_DECIDED")
    if r.review_type == "committee_review" and body.decision == "approve" and cu.user.role not in (Role.committee_member, Role.admin):
        raise Forbidden("Committee reviews can only be approved by committee members", code="COMMITTEE_ONLY")
    r.decision, r.decision_rationale = body.decision, body.rationale
    r.status = DECISION_STATUS[body.decision]
    r.decided_by, r.decided_at = cu.user.id, utcnow()
    record_event(db, "review.decision", actor=cu.user, resource_type="review", resource_id=r.id, case_id=r.case_id,
                 details={"decision": body.decision}, commit=False)
    db.commit()
    return {"review": _payload(db, r)}
