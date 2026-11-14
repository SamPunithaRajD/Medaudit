"""Validated (Pydantic) shapes for extracted clinical items. Every item carries its source reference."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Status = Literal["documented", "normalized", "missing", "uncertain"]
Precision = Literal["second", "minute", "hour", "date", "unknown"]


class SourceRef(BaseModel):
    document_id: str
    page_number: int = Field(ge=1)
    excerpt: str = Field(max_length=2000)
    line_index: int = 0

    @field_validator("excerpt")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class Timestamped(BaseModel):
    at: datetime | None = None
    precision: Precision = "unknown"
    basis: Literal["explicit", "page_context_date", "none"] = "none"
    raw_time: str | None = None


class PatientField(BaseModel):
    field: str
    value: str | None
    normalized: dict | None = None
    status: Status
    source: SourceRef | None = None
    method: Literal["rule", "llm", "human"] = "rule"


class VitalItem(Timestamped):
    measurement_type: str
    value: float | None
    value_text: str
    unit: str | None
    original_unit: str | None = None
    status: Status
    source: SourceRef


class MedicationItem(BaseModel):
    drug_name: str = Field(min_length=2, max_length=200)
    dose_value: float | None = None
    dose_unit: str | None = None
    route: str | None = None
    frequency: str | None = None
    record_kind: Literal["order", "administration", "change", "unspecified"] = "unspecified"
    time: Timestamped = Field(default_factory=Timestamped)
    uncertainty: list[str] = Field(default_factory=list)
    status: Status = "documented"
    source: SourceRef


class LabItem(BaseModel):
    test_name: str
    test_code: str
    value_num: float | None
    value_text: str
    unit: str | None
    reference_range_text: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    abnormal_flag: Literal["high", "low"] | None = None
    abnormal_basis: str | None = None
    sampled: Timestamped = Field(default_factory=Timestamped)
    resulted: Timestamped = Field(default_factory=Timestamped)
    status: Status = "documented"
    source: SourceRef


class EventItem(Timestamped):
    type: str
    subtype: str | None = None
    title: str
    description: str
    status: Status = "documented"
    has_signature_marker: bool | None = None
    source: SourceRef
    agent: str
