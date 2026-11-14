from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.types import EncryptedText, JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow


class PatientCase(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "patient_cases"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("CASE"))
    patient_ref: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    # Direct identifiers are encrypted at rest (AES-256-GCM column encryption).
    patient_name: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    patient_identifier: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    age_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(40), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    ward: Mapped[str | None] = mapped_column(String(120), nullable=True)
    icu_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    admitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    admitted_at_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    outcome_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    outcome_at_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    cause_as_recorded: Mapped[str | None] = mapped_column(Text, nullable=True)
    death_location: Mapped[str] = mapped_column(String(20), default="unknown")
    status: Mapped[str] = mapped_column(String(40), default="processing", index=True)
    review_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    processing_state: Mapped[str] = mapped_column(String(40), default="uploaded")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id", ondelete="SET NULL"), nullable=True,
                                                   index=True)
    review_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    review_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Which field came from which page / from the uploader / was edited by a human.
    field_provenance: Mapped[dict] = mapped_column(JSONType, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONType, default=dict)

    assignments: Mapped[list["CaseAssignment"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class CaseAssignment(Base):
    __tablename__ = "case_assignments"
    __table_args__ = (UniqueConstraint("case_id", "user_id", name="case_user"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ASG"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    assignment_role: Mapped[str] = mapped_column(String(40), default="reviewer")  # uploader|reviewer|observer|emergency
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # time-limited grants
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    granted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    case: Mapped[PatientCase] = relationship(back_populates="assignments")
