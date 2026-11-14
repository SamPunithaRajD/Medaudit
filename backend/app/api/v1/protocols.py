from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.audit_engine.clinical_rules.engine import active_rule_sets, evaluate_case
from app.audit_engine.clinical_rules.rules import ProtocolRules
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.models import HospitalProtocol, ProtocolVersion
from app.schemas.requests import ProtocolCompare, ProtocolCreate, ProtocolVersionCreate
from app.security.access_control import CurrentUser, get_case_for_user
from app.security.audit_logging import record_event
from app.utils.ids import iso, utcnow

router = APIRouter(prefix="/api/v1/protocols", tags=["protocols"])


def _validate_rules(rules: dict) -> dict:
    try:
        return ProtocolRules.model_validate(rules).model_dump(mode="json")
    except ValidationError as exc:
        raise ValidationFailed("Protocol rules are invalid", details=[
            {"field": "rules." + ".".join(str(x) for x in e["loc"]), "issue": e["msg"]} for e in exc.errors()[:20]]) from exc


def _version(v: ProtocolVersion) -> dict:
    return {"id": v.id, "version": v.version, "status": v.status, "effective_date": v.effective_date.isoformat() if v.effective_date else None,
            "review_date": v.review_date.isoformat() if v.review_date else None, "rules": v.rules, "change_note": v.change_note,
            "created_by": v.created_by, "created_at": iso(v.created_at), "approved_by": v.approved_by,
            "approved_at": iso(v.approved_at)}


def _protocol(p: HospitalProtocol, include_versions: bool = True) -> dict:
    approved = [v for v in p.versions if v.status == "approved"]
    current = max(approved, key=lambda v: v.approved_at or v.created_at) if approved else None
    out = {"id": p.id, "name": p.name, "category": p.category, "responsible_department": p.responsible_department,
           "description": p.description, "current_version": _version(current) if current else None,
           "approved_status": "approved" if current else "draft", "created_at": iso(p.created_at),
           "updated_at": iso(p.updated_at)}
    if include_versions:
        out["versions"] = [_version(v) for v in p.versions]
    return out


def _get(db: Session, protocol_id: str) -> HospitalProtocol:
    p = db.get(HospitalProtocol, protocol_id)
    if p is None or p.deleted_at is not None:
        raise NotFound("Protocol not found", code="PROTOCOL_NOT_FOUND")
    return p


@router.get("")
def list_protocols(p: Pagination = Depends(pagination), category: str | None = None, db: Session = Depends(get_db),
                   cu: CurrentUser = Depends(require("protocol.read"))):
    q = select(HospitalProtocol).where(HospitalProtocol.deleted_at.is_(None))
    if category:
        q = q.where(HospitalProtocol.category == category)
    return paginate(db, q.order_by(HospitalProtocol.name), p, lambda rows: [_protocol(x, False) for x in rows])


@router.post("", status_code=201)
def create_protocol(body: ProtocolCreate, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("protocol.manage"))):
    if db.scalar(select(HospitalProtocol).where(HospitalProtocol.name == body.name)):
        raise Conflict("A protocol with this name exists", code="PROTOCOL_EXISTS")
    p = HospitalProtocol(name=body.name, category=body.category, responsible_department=body.responsible_department,
                         description=body.description, created_by=cu.user.id)
    db.add(p)
    db.flush()
    db.add(ProtocolVersion(protocol_id=p.id, version=body.version, status="draft", effective_date=body.effective_date,
                           review_date=body.review_date, rules=_validate_rules(body.rules), change_note=body.change_note,
                           created_by=cu.user.id))
    record_event(db, "protocol.create", actor=cu.user, resource_type="protocol", resource_id=p.id, commit=False)
    db.commit()
    db.refresh(p)
    return {"protocol": _protocol(p)}


@router.get("/{protocol_id}")
def get_protocol(protocol_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("protocol.read"))):
    return {"protocol": _protocol(_get(db, protocol_id))}


@router.post("/{protocol_id}/versions", status_code=201)
def add_version(protocol_id: str, body: ProtocolVersionCreate, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("protocol.manage"))):
    p = _get(db, protocol_id)
    if any(v.version == body.version for v in p.versions):
        raise Conflict("Version already exists", code="VERSION_EXISTS")
    v = ProtocolVersion(protocol_id=p.id, version=body.version, status="draft", effective_date=body.effective_date,
                        review_date=body.review_date, rules=_validate_rules(body.rules), change_note=body.change_note,
                        created_by=cu.user.id)
    db.add(v)
    record_event(db, "protocol.version_create", actor=cu.user, resource_type="protocol", resource_id=p.id,
                 details={"version": body.version}, commit=False)
    db.commit()
    db.refresh(p)
    return {"protocol": _protocol(p)}


@router.post("/{protocol_id}/versions/{version_id}/approve")
def approve_version(protocol_id: str, version_id: str, db: Session = Depends(get_db),
                    cu: CurrentUser = Depends(require("protocol.manage"))):
    p = _get(db, protocol_id)
    v = next((x for x in p.versions if x.id == version_id), None)
    if v is None:
        raise NotFound("Version not found")
    if v.status == "approved":
        raise Conflict("Version already approved")
    for other in p.versions:
        if other.status == "approved":
            other.status = "retired"
    v.status, v.approved_by, v.approved_at = "approved", cu.user.id, utcnow()
    record_event(db, "protocol.approve", actor=cu.user, resource_type="protocol", resource_id=p.id,
                 details={"version": v.version}, commit=False)
    db.commit()
    return {"protocol": _protocol(p)}


@router.post("/{protocol_id}/compare")
def compare(protocol_id: str, body: ProtocolCompare, db: Session = Depends(get_db),
            cu: CurrentUser = Depends(require("protocol.read"))):
    """Dry-run a protocol version against a case's documented events. Nothing is persisted."""
    p = _get(db, protocol_id)
    case = get_case_for_user(db, cu.user, body.case_id)
    if body.version_id:
        if not any(v.id == body.version_id for v in p.versions):
            raise NotFound("Version not found for this protocol")
        sets, warnings = active_rule_sets(db, version_id=body.version_id)
    else:
        sets, warnings = active_rule_sets(db, protocol_id=p.id)
    if not sets:
        raise ValidationFailed("No approved (or specified) version of this protocol is available to compare",
                               details=[{"field": "version_id", "issue": "No applicable version"}])
    cands, meta = evaluate_case(db, case, rule_sets=sets, include_builtin=False)
    rs = sets[0]
    record_event(db, "protocol.compare", actor=cu.user, resource_type="protocol", resource_id=p.id, case_id=case.id,
                 details={"version": rs.version.version})
    return {"protocol_id": p.id, "protocol_name": p.name, "protocol_version": rs.version.version,
            "protocol_version_id": rs.version.id, "protocol_version_status": rs.version.status, "case_id": case.id,
            "rules_evaluated": len(rs.rules.rules),
            "deviations": [{"rule_id": c.rule_id, "title": c.title, "category": c.category, "severity": c.severity,
                            "description": c.description, "occurred_at": iso(c.occurred_at),
                            "measurements": {k: v for k, v in c.measurements.items() if k != "rule"},
                            "evidence": [{"document_id": e.document_id, "page_number": e.page_number,
                                          "excerpt": e.excerpt, "event_at": iso(e.event_at)} for e in c.evidence],
                            "uncertainty": c.uncertainty} for c in cands],
            "persisted": False, "warnings": warnings + meta["warnings"],
            "notice": "Comparison results are deterministic rule outputs for reviewer consideration, not conclusions."}
