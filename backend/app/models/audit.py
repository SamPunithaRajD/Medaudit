from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.types import EncryptedText, JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow


class AuditFinding(Base, TimestampMixin):
    __tablename__ = "audit_findings"
    __table_args__ = (UniqueConstraint("case_id", "fingerprint", name="case_fingerprint"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("FND"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    rule_id: Mapped[str] = mapped_column(String(80), index=True)
    rule_source: Mapped[str] = mapped_column(String(40))  # hospital_protocol | builtin_documentation_check
    protocol_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    protocol_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_method: Mapped[str] = mapped_column(String(20), default="unavailable")
    uncertainty: Mapped[list] = mapped_column(JSONType, default=list)
    measurements: Mapped[dict] = mapped_column(JSONType, default=dict)
    requires_human_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")  # evidence check
    review_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)  # human review
    amended_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amended_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)


class FindingReview(Base):
    """Immutable review history for a finding."""
    __tablename__ = "finding_reviews"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("FRV"))
    finding_id: Mapped[str] = mapped_column(ForeignKey("audit_findings.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    review_status: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Comment(Base):
    """Reviewer comments attached to findings, recommendations, reviews or counterfactual analyses."""
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CMT"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(30), index=True)
    target_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PreventabilityAssessment(Base):
    __tablename__ = "preventability_assessments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PVA"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    assessment_type: Mapped[str] = mapped_column(String(30))  # preliminary | clinical_review | committee_decision
    category: Mapped[str] = mapped_column(String(30))
    rationale: Mapped[str] = mapped_column(Text, default="")
    framework_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    framework_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[list] = mapped_column(JSONType, default=list)
    inputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    contributing_factors: Mapped[list] = mapped_column(JSONType, default=list)
    uncertainties: Mapped[list] = mapped_column(JSONType, default=list)
    missing_information: Mapped[list] = mapped_column(JSONType, default=list)
    supporting_finding_ids: Mapped[list] = mapped_column(JSONType, default=list)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("REC"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("audit_findings.id", ondelete="SET NULL"), nullable=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20))
    suggested_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    source: Mapped[str] = mapped_column(String(30), default="human")  # protocol_template | builtin_template | human
    protocol_ref: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    supporting_evidence: Mapped[list] = mapped_column(JSONType, default=list)
    tracked_as_action: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CorrectiveActionUpdate(Base):
    """Append-only status history for corrective actions (recommendations being tracked)."""
    __tablename__ = "corrective_action_updates"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CAU"))
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Review(Base, TimestampMixin):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RVW"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    review_type: Mapped[str] = mapped_column(String(30), default="case_review")
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AuditReport(Base):
    __tablename__ = "audit_reports"
    __table_args__ = (UniqueConstraint("case_id", "version", name="case_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("RPT"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    generated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    content: Mapped[str] = mapped_column(EncryptedText)  # encrypted JSON
    content_sha256: Mapped[str] = mapped_column(String(64))


class CounterfactualAnalysis(Base):
    __tablename__ = "counterfactual_analyses"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CFA"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    scenario_type: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
