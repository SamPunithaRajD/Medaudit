from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.core.config import get_settings
from app.core.exceptions import NotFound
from app.models import AssistantConversation, AssistantMessage, Comment, CounterfactualAnalysis
from app.schemas.requests import AssistantMessageRequest, CommentCreate, CounterfactualRequest
from app.schemas.responses import AssistantResponse
from app.security.access_control import CurrentUser, can_access_case, get_case_for_user
from app.security.audit_logging import record_event
from app.security.rate_limit import limiter
from app.services import assistant_service, counterfactual_service
from app.services.serializers import comments_for
from app.utils.ids import iso

router = APIRouter(prefix="/api/v1", tags=["assistant"])


def _msg(m: AssistantMessage) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "created_at": iso(m.created_at), "intent": m.intent,
            "provisional": m.provisional, "evidence": m.evidence or [], "limitations": m.limitations or [],
            "requires_human_verification": m.requires_human_verification, "llm_used": m.llm_used}


def _conversation(db: Session, cu: CurrentUser, conversation_id: str) -> AssistantConversation:
    conv = db.get(AssistantConversation, conversation_id)
    # Conversations are private to their author and still require current case access.
    if conv is None or conv.user_id != cu.user.id or not can_access_case(db, cu.user, conv.case_id):
        raise NotFound("Conversation not found", code="CONVERSATION_NOT_FOUND")
    return conv


@router.post("/assistant/message", response_model=AssistantResponse)
def assistant_message(body: AssistantMessageRequest, db: Session = Depends(get_db),
                      cu: CurrentUser = Depends(require("assistant.use"))):
    limiter.hit(f"assistant:{cu.user.id}", get_settings().sensitive_rate_limit_per_minute)
    case = get_case_for_user(db, cu.user, body.case_id, "assistant.use")
    if body.conversation_id:
        conv = _conversation(db, cu, body.conversation_id)
        if conv.case_id != case.id:
            raise NotFound("Conversation not found", code="CONVERSATION_NOT_FOUND")
    else:
        conv = AssistantConversation(case_id=case.id, user_id=cu.user.id, title=body.message[:80])
        db.add(conv)
        db.flush()
    db.add(AssistantMessage(conversation_id=conv.id, role="user", content=body.message, provisional=False,
                            requires_human_verification=False))
    ans = assistant_service.answer(db, case, body.message)
    content, llm_used = assistant_service.phrase(ans, body.message)
    limitations = list(dict.fromkeys(ans.limitations + ([] if ans.evidence else ["No evidence references were available for this answer."])))
    reply = AssistantMessage(conversation_id=conv.id, role="assistant", content=content, intent=ans.intent,
                             evidence=ans.evidence, limitations=limitations, provisional=True,
                             requires_human_verification=ans.intent not in ("help", "declined_advice"), llm_used=llm_used)
    db.add(reply)
    db.flush()
    record_event(db, "assistant.message", actor=cu.user, resource_type="conversation", resource_id=conv.id,
                 case_id=case.id, details={"intent": ans.intent, "llm_used": llm_used}, commit=False)
    db.commit()
    return {"conversation_id": conv.id, "case_id": case.id, "message": _msg(reply)}


@router.get("/assistant/conversations")
def conversations(p: Pagination = Depends(pagination), case_id: str | None = None, db: Session = Depends(get_db),
                  cu: CurrentUser = Depends(require("assistant.use"))):
    q = select(AssistantConversation).where(AssistantConversation.user_id == cu.user.id)
    if case_id:
        get_case_for_user(db, cu.user, case_id, "assistant.use")
        q = q.where(AssistantConversation.case_id == case_id)
    res = paginate(db, q.order_by(AssistantConversation.updated_at.desc()), p,
                   lambda rows: [{"id": c.id, "case_id": c.case_id, "title": c.title, "created_at": iso(c.created_at),
                                  "updated_at": iso(c.updated_at)} for c in rows if can_access_case(db, cu.user, c.case_id)])
    return res


@router.get("/assistant/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, db: Session = Depends(get_db),
                        cu: CurrentUser = Depends(require("assistant.use"))):
    conv = _conversation(db, cu, conversation_id)
    return {"id": conv.id, "case_id": conv.case_id, "title": conv.title, "messages": [_msg(m) for m in conv.messages]}


@router.post("/cases/{case_id}/counterfactual", status_code=201)
def counterfactual(case_id: str, body: CounterfactualRequest, db: Session = Depends(get_db),
                   cu: CurrentUser = Depends(require("counterfactual.use"))):
    case = get_case_for_user(db, cu.user, case_id, "counterfactual.use")
    result = counterfactual_service.analyse(db, case, body.scenario_type, body.question, body.minutes_earlier)
    row = CounterfactualAnalysis(case_id=case.id, created_by=cu.user.id, scenario_type=body.scenario_type,
                                 question=body.question, result=result)
    db.add(row)
    db.flush()
    record_event(db, "counterfactual.create", actor=cu.user, resource_type="counterfactual", resource_id=row.id,
                 case_id=case.id, commit=False)
    db.commit()
    return {"id": row.id, "case_id": case.id, **result, "created_at": iso(row.created_at)}


@router.get("/cases/{case_id}/counterfactual")
def list_counterfactuals(case_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("counterfactual.use"))):
    case = get_case_for_user(db, cu.user, case_id, "counterfactual.use")
    rows = db.scalars(select(CounterfactualAnalysis).where(CounterfactualAnalysis.case_id == case.id)
                      .order_by(CounterfactualAnalysis.created_at.desc())).all()
    cm = comments_for(db, "counterfactual", [r.id for r in rows])
    return {"items": [{"id": r.id, "case_id": r.case_id, **r.result, "reviewer_comments": cm.get(r.id, []),
                       "created_at": iso(r.created_at)} for r in rows]}


@router.post("/counterfactual/{analysis_id}/comments", status_code=201)
def comment_counterfactual(analysis_id: str, body: CommentCreate, db: Session = Depends(get_db),
                           cu: CurrentUser = Depends(require("counterfactual.use"))):
    row = db.get(CounterfactualAnalysis, analysis_id)
    if row is None:
        raise NotFound("Analysis not found")
    get_case_for_user(db, cu.user, row.case_id, "counterfactual.use")
    c = Comment(case_id=row.case_id, target_type="counterfactual", target_id=row.id, author_id=cu.user.id, body=body.body)
    db.add(c)
    db.commit()
    return {"comment": {"id": c.id, "body": c.body, "created_at": iso(c.created_at)}}
