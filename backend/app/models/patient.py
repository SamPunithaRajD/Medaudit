"""Doctor-owned patients and explicit sharing. Cases link to a patient; access flows from ownership/sharing."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.types import EncryptedText, UTCDateTime
from app.utils.ids import new_id, utcnow


class Patient(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("owner_user_id", "patient_ref", name="owner_ref"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PAT"))
    # RESTRICT: archiving a doctor never deletes or orphans their patients.
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    patient_ref: Mapped[str] = mapped_column(String(120))  # hospital reference (MRN/UHID or local ref)
    display_name: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    age_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(40), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)


class PatientAccess(Base):
    """Explicit share of a patient (and its cases) with another user."""
    __tablename__ = "patient_access"
    __table_args__ = (UniqueConstraint("patient_id", "user_id", name="patient_user"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PAC"))
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    access_level: Mapped[str] = mapped_column(String(20), default="read")  # read | contribute
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
