"""Request bodies (validated). Actors are always taken from the server session, never from bodies."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (CaseStatus, FindingCategory, PreventabilityCategory, RecommendationCategory,
                              RecommendationStatus, ReviewStatus, Role, Severity)


# Internal hospital domains (.local, .lan) are common, so only a basic shape check is applied.
EMAIL = r"^[^@\s]{1,64}@[^@\s]{1,190}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(Strict):
    """Sign in with a Doctor/Admin ID (`login_id`). Staff without an ID may use their email."""
    login_id: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)  # backwards compatible alias
    password: str = Field(min_length=1, max_length=256)
    remember_me: bool = False

    @model_validator(mode="after")
    def _one_identifier(self):
        if not (self.login_id or self.email):
            raise ValueError("login_id is required")
        return self


class ChangePasswordRequest(Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
    confirm_password: str | None = Field(default=None, max_length=256)


class ActivateRequest(Strict):
    doctor_id: str = Field(min_length=3, max_length=40)
    email: str = Field(pattern=EMAIL, max_length=255)
    activation_code: str = Field(min_length=6, max_length=40)
    password: str = Field(min_length=1, max_length=256)
    confirm_password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(Strict):
    login_id: str = Field(min_length=1, max_length=255)


class ResetPasswordRequest(Strict):
    login_id: str = Field(min_length=1, max_length=255)
    reset_code: str = Field(min_length=6, max_length=40)
    new_password: str = Field(min_length=1, max_length=256)
    confirm_password: str = Field(min_length=1, max_length=256)


# --- administration -------------------------------------------------------------------

class DoctorCreate(Strict):
    doctor_id: str | None = Field(default=None, max_length=30)  # omit to auto-generate
    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(pattern=EMAIL, max_length=255)
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+()\- ]{6,40}$")
    department: str = Field(min_length=2, max_length=120)
    designation: str | None = Field(default=None, max_length=120)
    employee_ref: str | None = Field(default=None, max_length=80)
    valid_until: date | None = None


class DoctorUpdate(Strict):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: str | None = Field(default=None, pattern=EMAIL, max_length=255)
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+()\- ]{6,40}$")
    department: str | None = Field(default=None, min_length=2, max_length=120)
    designation: str | None = Field(default=None, max_length=120)
    employee_ref: str | None = Field(default=None, max_length=80)
    valid_until: date | None = None
    expected_updated_at: datetime | None = None


class StatusChange(Strict):
    reason: str = Field(min_length=3, max_length=500)


class OptionalReason(Strict):
    reason: str | None = Field(default=None, max_length=500)


class ResetActivation(Strict):
    reason: str = Field(min_length=3, max_length=500)
    confirm_doctor_id: str = Field(min_length=3, max_length=40)


class PasswordResetInitiate(Strict):
    revoke_sessions: bool = True


class AuthSettingsUpdate(Strict):
    activation_token_hours: int | None = Field(default=None, ge=1, le=720)
    reset_token_hours: int | None = Field(default=None, ge=1, le=168)
    max_failed_logins: int | None = Field(default=None, ge=3, le=20)
    lockout_minutes: int | None = Field(default=None, ge=1, le=1440)
    max_activation_attempts: int | None = Field(default=None, ge=3, le=20)
    remember_me_days: int | None = Field(default=None, ge=1, le=30)
    allow_forgot_password_requests: bool | None = None
    allow_emergency_access: bool | None = None
    emergency_access_max_minutes: int | None = Field(default=None, ge=15, le=1440)


class EmergencyAccessRequest(Strict):
    reason: str = Field(min_length=20, max_length=500)
    duration_minutes: int = Field(default=60, ge=15, le=1440)


# --- doctor workspace -------------------------------------------------------------------

class DoctorProfileUpdate(Strict):
    """Doctors may edit contact details only; ID, role, name, department and designation are admin-controlled."""
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+()\- ]{6,40}$")
    contact_info: str | None = Field(default=None, max_length=500)


class PreferencesUpdate(Strict):
    theme: Literal["light", "dark", "system"] | None = None
    language: Literal["en"] | None = None
    notification_preferences: dict[str, bool] | None = None
    dashboard_preferences: dict[str, str | bool | int] | None = None


class PatientCreate(Strict):
    patient_ref: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=200)
    age_years: int | None = Field(default=None, ge=0, le=130)
    gender: Literal["male", "female", "other", "unknown"] | None = None
    department: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)


class PatientUpdate(Strict):
    display_name: str | None = Field(default=None, max_length=200)
    age_years: int | None = Field(default=None, ge=0, le=130)
    gender: Literal["male", "female", "other", "unknown"] | None = None
    department: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)


class PatientShare(Strict):
    doctor_id: str = Field(min_length=3, max_length=40)
    access_level: Literal["read", "contribute"] = "read"


class UserCreate(Strict):
    email: str = Field(pattern=EMAIL, max_length=255)
    display_name: str = Field(min_length=2, max_length=200)
    role: Role
    password: str = Field(min_length=1, max_length=256)
    department: str | None = Field(default=None, max_length=120)


class UserUpdate(Strict):
    display_name: str | None = Field(default=None, min_length=2, max_length=200)
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None
    department: str | None = Field(default=None, max_length=120)


class CaseUpdate(Strict):
    patient_ref: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    ward: str | None = Field(default=None, max_length=120)
    cause_as_recorded: str | None = Field(default=None, max_length=2000)
    death_location: Literal["in_hospital", "in_transit", "brought_dead", "unknown"] | None = None
    status: CaseStatus | None = None
    review_status: ReviewStatus | None = None
    expected_updated_at: datetime | None = None


class AssignmentRequest(Strict):
    user_id: str
    assignment_role: Literal["reviewer", "observer"] = "reviewer"


class FindingReviewRequest(Strict):
    action: Literal["accept", "reject", "amend", "comment"]
    review_status: ReviewStatus | None = None
    comment: str | None = Field(default=None, max_length=5000)
    amended_title: str | None = Field(default=None, max_length=255)
    amended_description: str | None = Field(default=None, max_length=5000)
    expected_updated_at: datetime | None = None


class PreventabilityReviewRequest(Strict):
    decision_type: Literal["clinical_review", "committee_decision"]
    category: PreventabilityCategory
    rationale: str = Field(min_length=10, max_length=10000)
    contributing_factors: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    supporting_finding_ids: list[str] = Field(default_factory=list, max_length=200)


class RecommendationCreate(Strict):
    case_id: str
    finding_id: str | None = None
    category: RecommendationCategory
    description: str = Field(min_length=5, max_length=5000)
    priority: Severity = Severity.moderate
    suggested_owner: str | None = Field(default=None, max_length=200)
    owner_id: str | None = None
    due_date: date | None = None


class RecommendationUpdate(Strict):
    status: RecommendationStatus | None = None
    owner_id: str | None = None
    due_date: date | None = None
    description: str | None = Field(default=None, min_length=5, max_length=5000)
    priority: Severity | None = None
    suggested_owner: str | None = Field(default=None, max_length=200)
    reviewer_comment: str | None = Field(default=None, max_length=5000)
    expected_updated_at: datetime | None = None


class ReviewCreate(Strict):
    case_id: str
    title: str = Field(min_length=3, max_length=255)
    review_type: Literal["case_review", "finding_review", "preventability_review", "committee_review"] = "case_review"
    assignee_id: str | None = None


class CommentCreate(Strict):
    body: str = Field(min_length=1, max_length=5000)


class ReviewDecision(Strict):
    decision: Literal["approve", "reject", "request_changes", "escalate_to_committee"]
    rationale: str | None = Field(default=None, max_length=10000)


class ProtocolCreate(Strict):
    name: str = Field(min_length=3, max_length=200)
    category: str = Field(min_length=2, max_length=60)
    responsible_department: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=5000)
    version: str = Field(min_length=1, max_length=40)
    effective_date: date | None = None
    review_date: date | None = None
    rules: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=2000)


class ProtocolVersionCreate(Strict):
    version: str = Field(min_length=1, max_length=40)
    effective_date: date | None = None
    review_date: date | None = None
    rules: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=2000)


class ProtocolCompare(Strict):
    case_id: str
    version_id: str | None = None


class AssistantMessageRequest(Strict):
    case_id: str
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class CounterfactualRequest(Strict):
    scenario_type: Literal["earlier_escalation", "earlier_icu_transfer", "earlier_lab_review", "custom"]
    question: str = Field(min_length=5, max_length=2000)
    minutes_earlier: int | None = Field(default=None, gt=0, le=10080)


class FindingFilter(BaseModel):
    category: FindingCategory | None = None
    severity: Severity | None = None
    review_status: ReviewStatus | None = None
