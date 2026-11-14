"""Recommendations derived only from verified findings + protocol/built-in templates. No free LLM generation."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditFinding, EvidenceReference, PatientCase, Recommendation
from app.utils.ids import utcnow


def due_date_for(priority: str):
    s = get_settings()
    days = {"critical": s.recommendation_due_days_critical, "high": s.recommendation_due_days_high,
            "moderate": s.recommendation_due_days_moderate}.get(priority, s.recommendation_due_days_low)
    return (utcnow() + timedelta(days=days)).date()


def generate(db: Session, case: PatientCase, job_id: str | None) -> dict:
    created = skipped = 0
    findings = db.scalars(select(AuditFinding).where(AuditFinding.case_id == case.id, AuditFinding.superseded.is_(False),
                                                     AuditFinding.review_status != "rejected")).all()
    existing = {r.finding_id for r in db.scalars(select(Recommendation).where(Recommendation.case_id == case.id))}
    for f in findings:
        tmpl = (f.measurements or {}).get("recommendation_template")
        if not tmpl or f.id in existing:
            skipped += 1
            continue
        if f.verification_status not in ("verified", "partially_verified", "absence_based"):
            skipped += 1
            continue
        evidence = [{"document_id": e.document_id, "page_number": e.page_number, "finding_id": f.id}
                    for e in db.scalars(select(EvidenceReference).where(EvidenceReference.finding_id == f.id)).all()[:10]]
        db.add(Recommendation(
            case_id=case.id, finding_id=f.id, category=tmpl["category"], description=tmpl["description"],
            priority=tmpl.get("priority") or "moderate", suggested_owner=tmpl.get("suggested_owner"),
            due_date=due_date_for(tmpl.get("priority") or "moderate"), status="open",
            source="builtin_template" if f.rule_source == "builtin_documentation_check" else "protocol_template",
            protocol_ref={"protocol_id": f.protocol_id, "version": f.protocol_version, "rule_id": f.rule_id},
            supporting_evidence=evidence, job_id=job_id))
        created += 1
    db.flush()
    return {"created": created, "skipped": skipped}
