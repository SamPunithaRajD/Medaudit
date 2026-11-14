from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.analytics import service
from app.api.deps import get_db, require
from app.security.access_control import CurrentUser
from app.security.audit_logging import record_event

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/overview")
def overview(from_: date | None = Query(None, alias="from"), to: date | None = None, department: str | None = None,
             review_status: str | None = None, finding_category: str | None = None, db: Session = Depends(get_db),
             cu: CurrentUser = Depends(require("analytics.read"))):
    record_event(db, "analytics.view", actor=cu.user, details={"view": "overview"})
    return service.overview(db, cu.user, frm=from_, to=to, department=department, review_status=review_status,
                            finding_category=finding_category)


@router.get("/departments")
def departments(from_: date | None = Query(None, alias="from"), to: date | None = None, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("analytics.read"))):
    return service.departments(db, cu.user, from_, to)


@router.get("/trends")
def trends(from_: date | None = Query(None, alias="from"), to: date | None = None, department: str | None = None,
           db: Session = Depends(get_db), cu: CurrentUser = Depends(require("analytics.read"))):
    return service.trends(db, cu.user, from_, to, department)


@router.get("/common-findings")
def common_findings(from_: date | None = Query(None, alias="from"), to: date | None = None,
                    department: str | None = None, category: str | None = None,
                    min_cases: int = Query(2, ge=2, le=100), db: Session = Depends(get_db),
                    cu: CurrentUser = Depends(require("analytics.read"))):
    return service.common_findings(db, cu.user, from_, to, department, category, min_cases)
