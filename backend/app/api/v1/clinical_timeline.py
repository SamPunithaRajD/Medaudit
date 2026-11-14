from __future__ import annotations

from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, paginate_list, require
from app.audit_engine.timeline.builder import ordered_events
from app.models import LaboratoryRecord, MedicationRecord, VitalObservation
from app.security.access_control import CurrentUser, get_case_for_user
from app.security.audit_logging import record_event
from app.services import serializers as S

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["clinical data"])


def _dt(d: date | None, end: bool = False) -> datetime | None:
    return datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc) if d else None


@router.get("/timeline")
def timeline(case_id: str, p: Pagination = Depends(pagination), type: list[str] | None = Query(None),
             from_: date | None = Query(None, alias="from"), to: date | None = None,
             q: str | None = Query(None, max_length=100), sort: str = Query("occurred_at:asc", pattern="^occurred_at:(asc|desc)$"),
             include_untimed: bool = True, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    events = ordered_events(db, case.id)
    if type:
        events = [e for e in events if e.type in type]
    lo, hi = _dt(from_), _dt(to, True)
    if lo or hi:
        # Untimed events cannot be placed in a date window; they are excluded from date-filtered views.
        events = [e for e in events if e.occurred_at and (not lo or e.occurred_at >= lo) and (not hi or e.occurred_at <= hi)]
    if not include_untimed:
        events = [e for e in events if e.occurred_at]
    if q:
        ql = q.lower()
        events = [e for e in events if ql in (e.title or "").lower() or ql in (e.description or "").lower()]
    if sort.endswith("desc"):
        timed = [e for e in events if e.occurred_at][::-1]
        events = timed + [e for e in events if not e.occurred_at]
    record_event(db, "case.timeline_view", actor=cu.user, resource_type="case", resource_id=case.id, case_id=case.id,
                 ip_hash=cu.ip_hash)
    page = paginate_list(events, p)
    page["items"] = S.events(db, page["items"])
    page["notes"] = ["Events without a reliable timestamp are listed after timed events in source order",
                     "Timestamps are never inferred; 'timestamp_basis' indicates how each time was obtained"]
    return page


@router.get("/vitals")
def vitals(case_id: str, p: Pagination = Depends(pagination), measurement: list[str] | None = Query(None),
           db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    q = select(VitalObservation).where(VitalObservation.case_id == case.id)
    if measurement:
        q = q.where(VitalObservation.measurement_type.in_(measurement))
    q = q.order_by(VitalObservation.observed_at.is_(None), VitalObservation.observed_at, VitalObservation.source_sequence)
    res = paginate(db, q, p, lambda rows: [S.vital(v) for v in rows])
    res["units"] = {"note": "Each measurement type has a single canonical unit; do not plot different types on one axis."}
    return res


@router.get("/medications")
def medications(case_id: str, p: Pagination = Depends(pagination), record_kind: str | None = None,
                db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    q = select(MedicationRecord).where(MedicationRecord.case_id == case.id)
    if record_kind:
        q = q.where(MedicationRecord.record_kind == record_kind)
    q = q.order_by(MedicationRecord.source_sequence)
    return paginate(db, q, p, lambda rows: [S.medication(m) for m in rows])


@router.get("/laboratory")
def laboratory(case_id: str, p: Pagination = Depends(pagination), test_code: str | None = None,
               abnormal_only: bool = False, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("case.read"))):
    case = get_case_for_user(db, cu.user, case_id)
    q = select(LaboratoryRecord).where(LaboratoryRecord.case_id == case.id)
    if test_code:
        q = q.where(LaboratoryRecord.test_code == test_code)
    if abnormal_only:
        q = q.where(LaboratoryRecord.abnormal_flag.is_not(None))
    q = q.order_by(LaboratoryRecord.source_sequence)
    return paginate(db, q, p, lambda rows: [S.laboratory(l) for l in rows])
