"""Response models documenting the contract shapes in OpenAPI. Extra fields (backend extensions) are allowed."""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorDetail(Open):
    field: str | None = None
    issue: str


class ErrorBody(Open):
    code: str
    message: str
    details: list[ErrorDetail] = []
    request_id: str


class ErrorResponse(Open):
    error: ErrorBody


class PaginationInfo(Open):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    next_page: int | None
    previous_page: int | None


class Page(Open, Generic[T]):
    items: list[T]
    pagination: PaginationInfo


class Region(Open):
    x: float
    y: float
    width: float
    height: float
    coordinate_space: str = "normalized_0_1"


class Evidence(Open):
    document_id: str | None
    page_number: int | None
    region: Region | None = None
    excerpt: str | None
    event_at: str | None
    verification_status: str
    extraction_confidence: float | None
    confidence_method: str


class PatientSummary(Open):
    display_name: str | None
    age_years: int | None
    gender: str | None


class Reviewer(Open):
    user_id: str
    display_name: str
    role: str


class Case(Open):
    id: str
    patient_ref: str | None
    patient: PatientSummary
    department: str | None
    ward: str | None
    admitted_at: str | None
    outcome_at: str | None
    cause_as_recorded: str | None
    death_location: str
    status: str
    review_status: str
    assigned_reviewers: list[Reviewer]
    created_at: str
    updated_at: str


class CaseDetail(Open):
    case: Case
    document_count: int
    finding_count: int
    recommendation_count: int


class UploadResponse(Open):
    case_id: str
    document_id: str
    job_id: str
    status: str
    created_at: str
    status_url: str
    estimated_seconds: float | None


class Stage(Open):
    key: str
    label: str
    status: str
    started_at: str | None
    completed_at: str | None


class JobError(Open):
    code: str
    message: str
    retryable: bool


class CaseStatusResponse(Open):
    case_id: str
    job_id: str | None
    status: str | None
    progress_percent: float | None
    stage: str | None
    stages: list[Stage]
    updated_at: str | None
    error: JobError | None


class Finding(Open):
    id: str
    case_id: str
    category: str
    title: str
    description: str
    severity: str
    evidence: list[Evidence]
    uncertainty: list[str]
    requires_human_verification: bool
    review_status: str
    reviewer_comments: list[dict[str, Any]]
    created_at: str


class ClinicalEvent(Open):
    id: str
    case_id: str
    occurred_at: str | None
    occurred_at_precision: str
    type: str
    title: str
    description: str
    severity: str | None
    evidence: list[Evidence]
    source_status: str


class Recommendation(Open):
    id: str
    case_id: str
    finding_id: str | None
    category: str
    description: str
    priority: str
    suggested_owner: str | None
    due_date: str | None
    status: str
    reviewer_comments: list[dict[str, Any]]
    created_at: str
    updated_at: str


class RecommendationEnvelope(Open):
    recommendation: Recommendation


class AssistantMessage(Open):
    role: str
    content: str
    created_at: str
    provisional: bool
    evidence: list[Evidence]
    limitations: list[str]
    requires_human_verification: bool


class AssistantResponse(Open):
    conversation_id: str
    message: AssistantMessage
