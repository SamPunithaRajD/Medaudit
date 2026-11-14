"""Enumerations. Values follow documentation/frontend-backend-contract.md."""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    doctor = "doctor"
    nurse = "nurse"
    admin = "admin"
    auditor = "auditor"
    committee_member = "committee_member"


class AccountStatus(StrEnum):
    active = "active"
    disabled = "disabled"
    locked = "locked"


class CaseStatus(StrEnum):
    processing = "processing"
    pending_review = "pending_review"
    under_review = "under_review"
    committee_review = "committee_review"
    approved = "approved"
    corrective_action_tracking = "corrective_action_tracking"
    closed = "closed"
    archived = "archived"
    failed = "failed"


class ProcessingState(StrEnum):
    """Case-level processing milestones (spec §5)."""
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    ocr_completed = "ocr_completed"
    ai_analysis_completed = "ai_analysis_completed"
    verification_pending = "verification_pending"
    completed = "completed"
    failed = "failed"


class ReviewStatus(StrEnum):
    pending = "pending"
    under_review = "under_review"
    committee_review = "committee_review"
    approved = "approved"
    rejected = "rejected"
    amended = "amended"


class FindingCategory(StrEnum):
    medical_management = "medical_management"
    nursing_monitoring = "nursing_monitoring"
    documentation_quality = "documentation_quality"
    system_process = "system_process"


class Severity(StrEnum):
    informational = "informational"  # backend extension; see backend-api-contract.md
    low = "low"
    moderate = "moderate"
    high = "high"
    critical = "critical"


class PreventabilityCategory(StrEnum):
    preventable = "preventable"
    possibly_preventable = "possibly_preventable"
    unavoidable = "unavoidable"
    undetermined = "undetermined"  # "Insufficient evidence"


class RecommendationStatus(StrEnum):
    open = "open"
    in_progress = "in_progress"
    complete = "complete"
    reopened = "reopened"
    cancelled = "cancelled"


class RecommendationCategory(StrEnum):
    treatment = "treatment"
    nursing = "nursing"
    documentation = "documentation"
    system_process = "system_process"
    equipment = "equipment"
    staff_training = "staff_training"


class DeathLocation(StrEnum):
    in_hospital = "in_hospital"
    in_transit = "in_transit"
    brought_dead = "brought_dead"
    unknown = "unknown"


class JobStatus(StrEnum):
    queued = "queued"
    validating = "validating"
    ingesting = "ingesting"
    preprocessing = "preprocessing"
    extracting = "extracting"
    classifying = "classifying"
    analyzing = "analyzing"
    building_timeline = "building_timeline"
    verifying_evidence = "verifying_evidence"
    generating_report = "generating_report"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_JOB_STATUSES = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}


class StageStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class PageStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    ocr_completed = "ocr_completed"
    needs_manual_review = "needs_manual_review"
    failed = "failed"


class DocumentStatus(StrEnum):
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class PageType(StrEnum):
    doctor_notes = "doctor_notes"
    nursing_notes = "nursing_notes"
    vitals = "vitals"
    laboratory = "laboratory"
    medication = "medication"
    prescription = "prescription"
    icu = "icu"
    emergency = "emergency"
    cpr = "cpr"
    radiology = "radiology"
    discharge_summary = "discharge_summary"
    death_summary = "death_summary"
    unknown = "unknown"


class EventType(StrEnum):
    doctor_note = "doctor_note"
    nursing_note = "nursing_note"
    vital = "vital"
    laboratory = "laboratory"
    medication = "medication"
    emergency = "emergency"
    cpr = "cpr"
    icu_escalation = "icu_escalation"
    procedure = "procedure"
    other = "other"


class TimePrecision(StrEnum):
    second = "second"
    minute = "minute"
    hour = "hour"
    date = "date"
    unknown = "unknown"


class SourceStatus(StrEnum):
    extracted = "extracted"
    human_verified = "human_verified"
    uncertain = "uncertain"


class EvidenceVerification(StrEnum):
    unverified = "unverified"
    verified = "verified"
    disputed = "disputed"


class ConfidenceMethod(StrEnum):
    calibrated = "calibrated"
    heuristic = "heuristic"
    human = "human"
    unavailable = "unavailable"


class EntityStatus(StrEnum):
    documented = "documented"   # explicitly present in source text
    normalized = "normalized"   # source value converted/interpreted (e.g. unit, date format)
    missing = "missing"         # expected but not found
    uncertain = "uncertain"     # present but ambiguous / low quality


class AgentStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class ProtocolStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    retired = "retired"
