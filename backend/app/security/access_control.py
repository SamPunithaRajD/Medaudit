"""Role-based and resource-level authorization. Every protected route resolves the user server-side.

Clinical-record access model (no role, including admin, gets blanket access):
  * a case is accessible if the user has an unexpired case assignment (uploader/owner, reviewer,
    observer, or a time-limited audited emergency grant), or
  * the case belongs to a patient the user owns or that was explicitly shared with them.
Administrators manage accounts and hospital configuration; they reach clinical records only through
an explicit assignment or the audited emergency-access procedure.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import Forbidden, NotFound
from app.models import CaseAssignment, Patient, PatientAccess, PatientCase, User
from app.models.enums import Role
from app.utils.ids import utcnow

CLINICAL = {Role.doctor, Role.nurse, Role.auditor, Role.committee_member}

# Permission → roles allowed. Case-scoped permissions additionally require case access.
PERMISSIONS: dict[str, set[str]] = {
    "case.upload": {Role.doctor, Role.nurse, Role.auditor, Role.admin},
    "case.read": CLINICAL | {Role.admin},
    "case.update": {Role.doctor, Role.admin, Role.auditor},
    "case.assign": {Role.admin},
    "case.process": {Role.doctor, Role.admin, Role.auditor},
    "document.read": CLINICAL | {Role.admin},
    "document.download": {Role.doctor, Role.admin, Role.auditor, Role.committee_member},
    "finding.review": CLINICAL | {Role.admin},
    "finding.approve": {Role.doctor, Role.admin, Role.committee_member},
    "preventability.clinical_review": {Role.doctor, Role.admin, Role.committee_member},
    "preventability.committee_decision": {Role.committee_member, Role.admin},
    "recommendation.manage": CLINICAL | {Role.admin},
    "review.manage": CLINICAL | {Role.admin},
    "review.decide": {Role.doctor, Role.admin, Role.committee_member},
    "report.export": {Role.doctor, Role.admin, Role.auditor, Role.committee_member},
    "analytics.read": {Role.doctor, Role.admin, Role.auditor, Role.committee_member},
    "protocol.read": CLINICAL | {Role.admin},
    "protocol.manage": {Role.admin},
    "assistant.use": CLINICAL | {Role.admin},
    "counterfactual.use": {Role.doctor, Role.admin, Role.auditor, Role.committee_member},
    "patient.manage": {Role.doctor},
    "clinical.emergency_access": {Role.admin, Role.committee_member},
    "admin.users": {Role.admin},
    "admin.doctors": {Role.admin},
    "admin.audit_logs": {Role.admin},
    "admin.settings": {Role.admin},
}


def has_permission(user: User, permission: str) -> bool:
    return user.role in PERMISSIONS.get(permission, set())


def require_permission(user: User, permission: str) -> None:
    if not has_permission(user, permission):
        raise Forbidden(code="FORBIDDEN")


# --- patients ----------------------------------------------------------------------

def _active_share(user_id: str):
    now = utcnow()
    return and_(PatientAccess.user_id == user_id, PatientAccess.revoked_at.is_(None),
                or_(PatientAccess.expires_at.is_(None), PatientAccess.expires_at > now))


def accessible_patients_query(user: User) -> Select:
    return select(Patient).where(Patient.deleted_at.is_(None), or_(
        Patient.owner_user_id == user.id,
        exists().where(PatientAccess.patient_id == Patient.id, _active_share(user.id))))


def patient_access_level(db: Session, user: User, patient: Patient) -> str | None:
    """'owner' | 'contribute' | 'read' | None"""
    if patient.deleted_at is not None:
        return None
    if patient.owner_user_id == user.id:
        return "owner"
    share = db.scalar(select(PatientAccess).where(PatientAccess.patient_id == patient.id, _active_share(user.id)))
    return share.access_level if share else None


def get_patient_for_user(db: Session, user: User, patient_id: str, need: str = "read") -> Patient:
    p = db.get(Patient, patient_id)
    level = patient_access_level(db, user, p) if p else None
    rank = {"read": 0, "contribute": 1, "owner": 2}
    if level is None or rank[level] < rank[need]:
        raise NotFound("Patient not found", code="PATIENT_NOT_FOUND")  # never reveal existence
    return p


# --- cases ---------------------------------------------------------------------------

def _active_assignment(user_id: str):
    now = utcnow()
    return and_(CaseAssignment.case_id == PatientCase.id, CaseAssignment.user_id == user_id,
                or_(CaseAssignment.expires_at.is_(None), CaseAssignment.expires_at > now))


def _case_access_clause(user: User):
    via_patient = exists().where(Patient.id == PatientCase.patient_id, Patient.deleted_at.is_(None), or_(
        Patient.owner_user_id == user.id,
        exists().where(PatientAccess.patient_id == Patient.id, _active_share(user.id))))
    return or_(exists().where(_active_assignment(user.id)), via_patient)


def accessible_cases_query(user: User) -> Select:
    return select(PatientCase).where(PatientCase.deleted_at.is_(None), _case_access_clause(user))


def can_access_case(db: Session, user: User, case_id: str) -> bool:
    return db.scalar(select(exists().where(PatientCase.id == case_id, PatientCase.deleted_at.is_(None),
                                           _case_access_clause(user)))) or False


def get_case_for_user(db: Session, user: User, case_id: str, permission: str = "case.read") -> PatientCase:
    """Returns the case or raises 404 (not 403) so inaccessible cases are indistinguishable from missing ones."""
    require_permission(user, permission)
    case = db.get(PatientCase, case_id)
    if case is None or case.deleted_at is not None or not can_access_case(db, user, case_id):
        raise NotFound("Case not found", code="CASE_NOT_FOUND")
    return case


@dataclass
class CurrentUser:
    user: User
    session_id: str
    ip_hash: str | None
