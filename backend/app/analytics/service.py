"""Hospital quality intelligence over de-identified aggregates.

Rules:
* Only reviewer-confirmed findings (approved/amended) count as institutional findings; unreviewed AI
  findings are reported separately and labelled as such.
* Final preventability uses committee decisions only; preliminary AI categories are shown separately.
* Counts between 1 and ANALYTICS_MIN_CELL_SIZE-1 are suppressed (null) to reduce re-identification risk.
* No patient identifiers are ever included.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditFinding, PatientCase, PreventabilityAssessment, Recommendation, User
from app.models.enums import Role

CATEGORIES = ["preventable", "possibly_preventable", "unavoidable", "undetermined"]
CONFIRMED = ("approved", "amended")


class Suppressor:
    def __init__(self) -> None:
        self.k = get_settings().analytics_min_cell_size
        self.applied = False

    def __call__(self, n: int | float | None) -> int | None:
        if n is None:
            return None
        n = int(n)
        if 0 < n < self.k:
            self.applied = True
            return None
        return n


def _scope_cases(db: Session, user: User, department: str | None, frm: date | None, to: date | None) -> pd.DataFrame:
    q = select(PatientCase.id, PatientCase.department, PatientCase.status, PatientCase.review_status,
               PatientCase.created_at, PatientCase.outcome_at, PatientCase.review_started_at,
               PatientCase.review_completed_at).where(PatientCase.deleted_at.is_(None))
    if user.role == Role.doctor and user.department:
        q = q.where(PatientCase.department == user.department)  # least privilege for clinicians
    if department:
        q = q.where(PatientCase.department == department)
    df = pd.DataFrame(db.execute(q).all(), columns=["id", "department", "status", "review_status", "created_at",
                                                     "outcome_at", "review_started_at", "review_completed_at"])
    if df.empty:
        return df
    for col in ("created_at", "outcome_at", "review_started_at", "review_completed_at"):
        df[col] = pd.to_datetime(df[col], utc=True)
    df["period_at"] = df["outcome_at"].fillna(df["created_at"])
    if frm:
        df = df[df["period_at"] >= pd.Timestamp(datetime.combine(frm, time.min, tzinfo=timezone.utc))]
    if to:
        df = df[df["period_at"] <= pd.Timestamp(datetime.combine(to, time.max, tzinfo=timezone.utc))]
    return df


def _findings(db: Session, case_ids: list[str], category: str | None) -> pd.DataFrame:
    if not case_ids:
        return pd.DataFrame(columns=["case_id", "category", "rule_id", "title", "review_status", "created_at"])
    q = select(AuditFinding.case_id, AuditFinding.category, AuditFinding.rule_id, AuditFinding.title,
               AuditFinding.review_status, AuditFinding.created_at).where(
        AuditFinding.case_id.in_(case_ids), AuditFinding.superseded.is_(False))
    if category:
        q = q.where(AuditFinding.category == category)
    df = pd.DataFrame(db.execute(q).all(), columns=["case_id", "category", "rule_id", "title", "review_status", "created_at"])
    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    return df


def _assessments(db: Session, case_ids: list[str], kind: str) -> dict[str, str]:
    if not case_ids:
        return {}
    rows = db.execute(select(PreventabilityAssessment.case_id, PreventabilityAssessment.category,
                             PreventabilityAssessment.created_at)
                      .where(PreventabilityAssessment.case_id.in_(case_ids),
                             PreventabilityAssessment.assessment_type == kind)
                      .order_by(PreventabilityAssessment.created_at)).all()
    latest: dict[str, str] = {}
    for cid, cat, _ in rows:
        latest[cid] = cat
    return latest


def overview(db: Session, user: User, *, frm: date | None = None, to: date | None = None, department: str | None = None,
             review_status: str | None = None, finding_category: str | None = None) -> dict:
    sup = Suppressor()
    cases = _scope_cases(db, user, department, frm, to)
    if review_status and not cases.empty:
        cases = cases[cases["review_status"] == review_status]
    ids = cases["id"].tolist() if not cases.empty else []
    approved = cases[cases["review_status"] == "approved"] if not cases.empty else cases
    final = _assessments(db, approved["id"].tolist() if not approved.empty else [], "committee_decision")
    prelim = _assessments(db, ids, "preliminary")
    f = _findings(db, ids, finding_category)
    confirmed = f[f["review_status"].isin(CONFIRMED)] if not f.empty else f
    pending = f[~f["review_status"].isin(CONFIRMED + ("rejected",))] if not f.empty else f

    avg_days = None
    if not approved.empty:
        start = approved["review_started_at"].fillna(approved["created_at"])
        durations = (approved["review_completed_at"] - start).dropna().dt.total_seconds() / 86400
        if len(durations) >= sup.k:
            avg_days = round(float(durations.mean()), 1)
        elif len(durations):
            sup.applied = True

    recs = db.execute(select(Recommendation.status).where(Recommendation.case_id.in_(ids),
                                                          Recommendation.tracked_as_action.is_(True))).scalars().all() if ids else []
    active = [s for s in recs if s != "cancelled"]
    rate = round(sum(1 for s in active if s == "complete") / len(active), 3) if len(active) >= sup.k else None
    if active and rate is None:
        sup.applied = True

    period_from = frm or (cases["period_at"].min().date() if not cases.empty else None)
    period_to = to or (cases["period_at"].max().date() if not cases.empty else None)
    monthly = []
    if not approved.empty:
        m = approved.dropna(subset=["review_completed_at"]).copy()
        if not m.empty:
            m["month"] = m["review_completed_at"].dt.strftime("%Y-%m")
            monthly = [{"month": k, "reviewed_count": sup(v)} for k, v in m.groupby("month").size().items()]

    return {
        "period": {"from": period_from.isoformat() if period_from else None, "to": period_to.isoformat() if period_to else None},
        "filters": {"department": department, "review_status": review_status, "finding_category": finding_category,
                    "from": frm.isoformat() if frm else None, "to": to.isoformat() if to else None},
        "total_cases_in_scope": sup(len(cases)),
        "total_deaths_reviewed": sup(len(approved)),
        "preventability_distribution": {c: sup(sum(1 for v in final.values() if v == c)) for c in CATEGORIES},
        "preliminary_preventability_distribution": {
            "label": "AI-assisted preliminary categories (not committee conclusions)",
            **{c: sup(sum(1 for v in prelim.values() if v == c)) for c in CATEGORIES}},
        "average_review_days": avg_days,
        "recurring_findings": [{"category": k, "count": sup(v)} for k, v in
                               (confirmed.groupby("category").size().items() if not confirmed.empty else [])],
        "unreviewed_findings": {"label": "Preliminary findings awaiting review (not confirmed)",
                                "count": sup(len(pending))},
        "department_comparison": [{"department": k or "Not documented", "case_count": sup(v)} for k, v in
                                  (cases.groupby(cases["department"].fillna("Not documented")).size().items() if not cases.empty else [])],
        "monthly_trends": monthly,
        "corrective_action_completion_rate": rate,
        "min_cell_size": sup.k,
        "suppression_applied": sup.applied,
    }


def departments(db: Session, user: User, frm: date | None, to: date | None) -> dict:
    sup = Suppressor()
    cases = _scope_cases(db, user, None, frm, to)
    out = []
    if not cases.empty:
        f = _findings(db, cases["id"].tolist(), None)
        conf = f[f["review_status"].isin(CONFIRMED)] if not f.empty else f
        for dept, grp in cases.groupby(cases["department"].fillna("Not documented")):
            ids = set(grp["id"])
            dconf = conf[conf["case_id"].isin(ids)] if not conf.empty else conf
            out.append({"department": dept, "case_count": sup(len(grp)),
                        "reviewed_count": sup(int((grp["review_status"] == "approved").sum())),
                        "confirmed_findings_by_category": {k: sup(v) for k, v in
                                                           (dconf.groupby("category").size().items() if not dconf.empty else [])}})
    return {"items": out, "min_cell_size": sup.k, "suppression_applied": sup.applied}


def trends(db: Session, user: User, frm: date | None, to: date | None, department: str | None) -> dict:
    sup = Suppressor()
    cases = _scope_cases(db, user, department, frm, to)
    items: list[dict] = []
    if not cases.empty:
        cases = cases.copy()
        cases["month"] = cases["period_at"].dt.strftime("%Y-%m")
        f = _findings(db, cases["id"].tolist(), None)
        conf = f[f["review_status"].isin(CONFIRMED)] if not f.empty else f
        month_of = dict(zip(cases["id"], cases["month"]))
        conf_month = conf["case_id"].map(month_of).value_counts().to_dict() if not conf.empty else {}
        for month, grp in cases.groupby("month"):
            items.append({"month": month, "case_count": sup(len(grp)),
                          "reviewed_count": sup(int((grp["review_status"] == "approved").sum())),
                          "confirmed_findings": sup(conf_month.get(month, 0))})
    return {"items": items, "min_cell_size": sup.k, "suppression_applied": sup.applied}


def common_findings(db: Session, user: User, frm: date | None, to: date | None, department: str | None,
                    category: str | None, min_cases: int = 2) -> dict:
    """Recurring pattern detection: confirmed findings sharing a rule across multiple cases."""
    sup = Suppressor()
    cases = _scope_cases(db, user, department, frm, to)
    items = []
    if not cases.empty:
        f = _findings(db, cases["id"].tolist(), category)
        conf = f[f["review_status"].isin(CONFIRMED)] if not f.empty else f
        if not conf.empty:
            for (rule_id, cat), grp in conf.groupby(["rule_id", "category"]):
                n_cases = grp["case_id"].nunique()
                items.append({"rule_id": rule_id, "category": cat, "title": grp["title"].iloc[0],
                              "finding_count": sup(len(grp)), "case_count": sup(n_cases),
                              "recurring": bool(n_cases >= min_cases)})
        items.sort(key=lambda x: -(x["finding_count"] or 0))
    return {"items": items, "basis": "reviewer-confirmed findings only", "min_cell_size": sup.k,
            "suppression_applied": sup.applied}
