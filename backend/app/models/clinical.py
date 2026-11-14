"""Structured clinical data extracted from case sheets. Every row carries its source document/page/excerpt."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow


class SourceMixin:
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_status: Mapped[str] = mapped_column(String(20), default="extracted")
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExtractedEntity(Base, SourceMixin):
    __tablename__ = "extracted_entities"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ENT"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    field_name: Mapped[str] = mapped_column(String(60))
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="documented")
    extraction_method: Mapped[str] = mapped_column(String(20), default="rule")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_method: Mapped[str] = mapped_column(String(20), default="unavailable")


class ClinicalEvent(Base, SourceMixin):
    __tablename__ = "clinical_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("EVT"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    occurred_at_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    timestamp_basis: Mapped[str] = mapped_column(String(30), default="none")  # explicit|page_context_date|none
    raw_time_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    type: Mapped[str] = mapped_column(String(30), index=True)
    subtype: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_method: Mapped[str] = mapped_column(String(20), default="unavailable")
    agent_name: Mapped[str] = mapped_column(String(60))
    source_sequence: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list] = mapped_column(JSONType, default=list)


class VitalObservation(Base, SourceMixin):
    __tablename__ = "vital_observations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("VIT"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("clinical_events.id", ondelete="SET NULL"), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    precision: Mapped[str] = mapped_column(String(10), default="unknown")
    timestamp_basis: Mapped[str] = mapped_column(String(30), default="none")
    measurement_type: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    original_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_sequence: Mapped[int] = mapped_column(Integer, default=0)


class MedicationRecord(Base, SourceMixin):
    __tablename__ = "medication_records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("MED"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    drug_name: Mapped[str] = mapped_column(String(200))
    dose_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    dose_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    route: Mapped[str | None] = mapped_column(String(30), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(30), nullable=True)
    record_kind: Mapped[str] = mapped_column(String(20), default="unspecified")  # order|administration|change|unspecified
    ordered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ordered_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    administered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    administered_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    uncertainty: Mapped[list] = mapped_column(JSONType, default=list)
    source_sequence: Mapped[int] = mapped_column(Integer, default=0)


class LaboratoryRecord(Base, SourceMixin):
    __tablename__ = "laboratory_records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("LAB"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    test_name: Mapped[str] = mapped_column(String(120))
    test_code: Mapped[str] = mapped_column(String(60), index=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference_range_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ref_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ref_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    abnormal_flag: Mapped[str | None] = mapped_column(String(10), nullable=True)  # high|low|null
    abnormal_basis: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sampled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sampled_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    resulted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resulted_precision: Mapped[str] = mapped_column(String(10), default="unknown")
    source_sequence: Mapped[int] = mapped_column(Integer, default=0)


class EvidenceReference(Base):
    __tablename__ = "evidence_references"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("EVD"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("audit_findings.id", ondelete="CASCADE"), nullable=True, index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("clinical_events.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")
    verification_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_method: Mapped[str] = mapped_column(String(20), default="unavailable")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
