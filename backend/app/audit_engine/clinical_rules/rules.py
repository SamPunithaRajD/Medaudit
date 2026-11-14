"""Hospital protocol rule schema (versioned JSON stored in protocol_versions.rules).

Example protocol version `rules` document:
{
  "rules": [
    {"rule_id": "ICU-OBS-01", "type": "max_observation_interval", "category": "nursing_monitoring",
     "severity": "moderate", "title": "Observation interval exceeded",
     "measurements": ["heart_rate", "bp_systolic", "spo2"], "max_minutes": 240,
     "recommendation": {"category": "nursing", "description": "Review observation frequency ...", "priority": "moderate"}}
  ]
}
No clinical threshold is built into the code: all numbers come from an approved protocol version.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.enums import FindingCategory, RecommendationCategory, Severity


class RecommendationTemplate(BaseModel):
    category: RecommendationCategory
    description: str = Field(min_length=5, max_length=2000)
    priority: Severity = Severity.moderate
    suggested_owner: str | None = None


class RuleBase(BaseModel):
    rule_id: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=3, max_length=200)
    category: FindingCategory
    severity: Severity
    description: str | None = None
    enabled: bool = True
    recommendation: RecommendationTemplate | None = None


class MaxObservationInterval(RuleBase):
    type: Literal["max_observation_interval"]
    measurements: list[str] = Field(min_length=1)
    max_minutes: int = Field(gt=0, le=10080)
    start_after_subtype: str | None = None  # e.g. "icu_transfer": only evaluate after this event


class EscalationAfterAbnormal(RuleBase):
    type: Literal["escalation_after_abnormal"]
    measurement: str
    operator: Literal["lt", "le", "gt", "ge"]
    value: float
    max_minutes: int = Field(gt=0, le=10080)
    escalation_types: list[str] = Field(default_factory=lambda: ["icu_escalation", "doctor_note"])


class LabReviewInterval(RuleBase):
    type: Literal["lab_review_interval"]
    tests: list[str] = Field(default_factory=list)  # empty = any test
    abnormal_only: bool = True
    max_minutes: int = Field(gt=0, le=10080)


class EventInterval(RuleBase):
    type: Literal["event_interval"]
    from_subtype: str
    to_subtype: str
    max_minutes: int = Field(gt=0, le=43200)
    flag_missing_target: bool = True


class MedicationAdministrationDocumented(RuleBase):
    type: Literal["medication_administration_documented"]
    window_minutes: int | None = Field(default=None, gt=0)


class RequiredDocumentation(RuleBase):
    type: Literal["required_documentation"]
    page_types: list[str] = Field(min_length=1)


Rule = Annotated[MaxObservationInterval | EscalationAfterAbnormal | LabReviewInterval | EventInterval
                 | MedicationAdministrationDocumented | RequiredDocumentation, Field(discriminator="type")]


class ProtocolRules(BaseModel):
    rules: list[Rule] = Field(default_factory=list)
    preventability_weights: dict[str, float] | None = None
