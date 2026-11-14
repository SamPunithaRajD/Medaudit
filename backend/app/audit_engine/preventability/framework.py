"""Transparent, versioned preliminary preventability framework.

The score is an *evidence-organisation index*, not a probability and not a validated instrument.
By design the system can only output `possibly_preventable` or `undetermined` as a preliminary
category; `preventable` and `unavoidable` are reserved for qualified human reviewers / the committee.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuditFinding, DocumentPage, PatientCase, PreventabilityAssessment, ProcessingJob

FRAMEWORK_ID = "MEDAUDIT-PRELIM-PREVENTABILITY"
FRAMEWORK_VERSION = "1.0.0"


@dataclass
class FrameworkConfig:
    category_weights: dict[str, float] = field(default_factory=lambda: {
        "medical_management": 1.0, "nursing_monitoring": 1.0, "system_process": 0.8, "documentation_quality": 0.3})
    severity_weights: dict[str, float] = field(default_factory=lambda: {
        "critical": 4.0, "high": 3.0, "moderate": 2.0, "low": 1.0, "informational": 0.0})
    possibly_preventable_threshold: float = 3.0
    max_failed_page_fraction: float = 0.2  # beyond this, evidence is insufficient
    min_timeline_events: int = 5
    allowed_preliminary_categories: tuple[str, ...] = ("possibly_preventable", "undetermined")


ASSESSMENT_AXES = {"medical_management": "Medical management", "nursing_monitoring": "Nursing and monitoring",
                   "system_process": "System and process", "documentation_quality": "Documentation"}


def assess(db: Session, case: PatientCase, job: ProcessingJob | None, config: FrameworkConfig | None = None,
           timeline_events: int = 0) -> PreventabilityAssessment:
    cfg = config or FrameworkConfig()
    findings = db.scalars(select(AuditFinding).where(AuditFinding.case_id == case.id, AuditFinding.superseded.is_(False))).all()
    pages = db.scalars(select(DocumentPage).where(DocumentPage.case_id == case.id)).all()

    contributions, excluded = [], []
    by_axis: dict[str, float] = {k: 0.0 for k in ASSESSMENT_AXES}
    for f in findings:
        if f.review_status == "rejected":
            excluded.append({"finding_id": f.id, "reason": "rejected by reviewer"})
            continue
        if f.verification_status not in ("verified", "partially_verified", "absence_based"):
            excluded.append({"finding_id": f.id, "reason": f"evidence {f.verification_status}"})
            continue
        cw = cfg.category_weights.get(f.category, 0.0)
        sw = cfg.severity_weights.get(f.severity, 0.0)
        pts = round(cw * sw, 3)
        if pts <= 0:
            continue
        by_axis[f.category] = round(by_axis.get(f.category, 0.0) + pts, 3)
        contributions.append({"finding_id": f.id, "title": f.title, "category": f.category, "severity": f.severity,
                              "category_weight": cw, "severity_weight": sw, "points": pts,
                              "verification_status": f.verification_status, "review_status": f.review_status})
    score = round(sum(c["points"] for c in contributions), 3)

    missing: list[str] = []
    uncertainties: list[str] = ["The score organises evidence for reviewers; it is not a validated probability."]
    failed = sum(1 for p in pages if p.status == "failed" or p.needs_manual_review)
    failed_frac = failed / max(len(pages), 1)
    if failed:
        missing.append(f"{failed} of {len(pages)} pages failed processing or need manual transcription")
    if not any(p.page_type == "death_summary" for p in pages):
        missing.append("No death summary identified")
    if case.outcome_at is None:
        missing.append("Time of death / outcome not documented in extracted data")
    if timeline_events < cfg.min_timeline_events:
        missing.append(f"Only {timeline_events} timeline events reconstructed")
    from app.audit_engine.clinical_rules.engine import active_rule_sets
    if not active_rule_sets(db)[0]:
        missing.append("No approved hospital protocol configured; clinical rules not evaluated")
    if job is not None and job.warnings:
        uncertainties.extend(str(w) for w in job.warnings[:5])

    insufficient = failed_frac > cfg.max_failed_page_fraction or timeline_events < cfg.min_timeline_events
    if insufficient:
        category = "undetermined"
        rationale = "Insufficient evidence: the extracted record is too incomplete for a preliminary assessment."
    elif score >= cfg.possibly_preventable_threshold:
        category = "possibly_preventable"
        rationale = (f"Verified findings contribute {score} points (threshold {cfg.possibly_preventable_threshold}). "
                     "This indicates evidence that reviewers should examine; it does not establish preventability, "
                     "causation or negligence.")
    else:
        category = "undetermined"
        rationale = ("Configured rules identified limited evidence-linked deviations. This does NOT establish that the "
                     "death was unavoidable; clinical review is required.")
    assert category in cfg.allowed_preliminary_categories

    factors = [{"axis": ASSESSMENT_AXES[a], "category": a, "points": p,
                "finding_ids": [c["finding_id"] for c in contributions if c["category"] == a]}
               for a, p in by_axis.items() if p > 0]

    db.execute(update(PreventabilityAssessment).where(PreventabilityAssessment.case_id == case.id,
                                                      PreventabilityAssessment.assessment_type == "preliminary")
               .values(is_current=False))
    pa = PreventabilityAssessment(
        case_id=case.id, assessment_type="preliminary", category=category, rationale=rationale,
        framework_id=FRAMEWORK_ID, framework_version=FRAMEWORK_VERSION, score=score, score_breakdown=contributions,
        inputs={"category_weights": cfg.category_weights, "severity_weights": cfg.severity_weights,
                "possibly_preventable_threshold": cfg.possibly_preventable_threshold,
                "max_failed_page_fraction": cfg.max_failed_page_fraction, "findings_considered": len(contributions),
                "findings_excluded": excluded, "pages": len(pages), "timeline_events": timeline_events},
        contributing_factors=factors, uncertainties=uncertainties, missing_information=missing,
        supporting_finding_ids=[c["finding_id"] for c in contributions], is_current=True)
    db.add(pa)
    db.flush()
    return pa
