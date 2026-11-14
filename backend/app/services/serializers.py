"""ORM → contract JSON (documentation/frontend-backend-contract.md). Missing values are null, never fabricated."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (AuditFinding, CaseAssignment, ClinicalEvent, Comment, Document, EvidenceReference,
                        LaboratoryRecord, MedicationRecord, PatientCase, Recommendation, User, VitalObservation)
from app.utils.ids import iso

EVIDENCE_STATUS = {"verified": "verified", "disputed": "disputed"}


def evidence(e: EvidenceReference) -> dict[str, Any]:
    return {"id": e.id, "document_id": e.document_id, "page_number": e.page_number, "region": e.region,
            "excerpt": e.excerpt, "event_at": iso(e.event_at),
            "verification_status": EVIDENCE_STATUS.get(e.verification_status, "unverified"),
            "verification_note": e.verification_note, "extraction_confidence": e.extraction_confidence,
            "confidence_method": e.confidence_method if e.extraction_confidence is not None else "unavailable",
            "event_id": e.event_id, "finding_id": e.finding_id}


def source_evidence(row) -> list[dict]:
    """Inline evidence for rows that carry their own source columns (vitals/meds/labs)."""
    if row.document_id is None:
        return []
    return [{"id": None, "document_id": row.document_id, "page_number": row.page_number, "region": None,
             "excerpt": row.excerpt, "event_at": None, "verification_status": "unverified", "verification_note": None,
             "extraction_confidence": None, "confidence_method": "unavailable", "event_id": getattr(row, "event_id", None),
             "finding_id": None}]


def comments_for(db: Session, target_type: str, ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    if not ids:
        return out
    rows = db.execute(select(Comment, User.display_name).join(User, User.id == Comment.author_id)
                      .where(Comment.target_type == target_type, Comment.target_id.in_(ids))
                      .order_by(Comment.created_at)).all()
    for c, name in rows:
        out[c.target_id].append({"id": c.id, "author": {"user_id": c.author_id, "display_name": name},
                                 "body": c.body, "created_at": iso(c.created_at)})
    return out


def case(db: Session, c: PatientCase, include_identity: bool = True) -> dict[str, Any]:
    reviewers = db.execute(select(User.id, User.display_name, User.role).join(CaseAssignment, CaseAssignment.user_id == User.id)
                           .where(CaseAssignment.case_id == c.id, CaseAssignment.assignment_role == "reviewer")).all()
    return {
        "id": c.id, "patient_ref": c.patient_ref,
        "patient": {"display_name": c.patient_name if include_identity else None, "age_years": c.age_years,
                    "gender": c.gender},
        "department": c.department, "ward": c.ward, "admitted_at": iso(c.admitted_at), "outcome_at": iso(c.outcome_at),
        "admitted_at_precision": c.admitted_at_precision, "outcome_at_precision": c.outcome_at_precision,
        "cause_as_recorded": c.cause_as_recorded, "death_location": c.death_location or "unknown",
        "status": c.status, "review_status": c.review_status, "processing_state": c.processing_state,
        "assigned_reviewers": [{"user_id": r.id, "display_name": r.display_name, "role": r.role} for r in reviewers],
        "field_provenance": c.field_provenance or {}, "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
    }


def document(d: Document) -> dict[str, Any]:
    return {"id": d.id, "case_id": d.case_id, "filename": d.filename, "media_type": d.media_type,
            "size_bytes": d.size_bytes, "page_count": d.page_count, "status": d.status, "sha256": d.sha256,
            "uploaded_at": iso(d.uploaded_at),
            "preview_url": f"/api/v1/documents/{d.id}/pages/1/preview" if d.page_count else None,
            "download_url": f"/api/v1/documents/{d.id}/download"}


def findings(db: Session, rows: list[AuditFinding]) -> list[dict[str, Any]]:
    ids = [f.id for f in rows]
    ev_map: dict[str, list[dict]] = defaultdict(list)
    if ids:
        for e in db.scalars(select(EvidenceReference).where(EvidenceReference.finding_id.in_(ids))):
            ev_map[e.finding_id].append(evidence(e))
    cm = comments_for(db, "finding", ids)
    out = []
    for f in rows:
        measurements = {k: v for k, v in (f.measurements or {}).items() if k != "recommendation_template"}
        out.append({
            "id": f.id, "case_id": f.case_id, "category": f.category,
            "title": f.amended_title or f.title, "description": f.amended_description or f.description,
            "original": {"title": f.title, "description": f.description} if (f.amended_title or f.amended_description) else None,
            "severity": f.severity, "evidence": ev_map.get(f.id, []), "uncertainty": f.uncertainty or [],
            "requires_human_verification": f.requires_human_verification, "review_status": f.review_status,
            "verification_status": f.verification_status, "reviewer_comments": cm.get(f.id, []),
            "rule": {"rule_id": f.rule_id, "source": f.rule_source, "protocol_id": f.protocol_id,
                     "protocol_version": f.protocol_version},
            "occurred_at": iso(f.occurred_at), "confidence": f.confidence, "confidence_method": f.confidence_method,
            "measurements": measurements, "provisional": f.review_status not in ("approved", "amended"),
            "created_at": iso(f.created_at), "updated_at": iso(f.updated_at),
        })
    return out


def events(db: Session, rows: list[ClinicalEvent]) -> list[dict[str, Any]]:
    ids = [e.id for e in rows]
    ev_map: dict[str, list[dict]] = defaultdict(list)
    if ids:
        for e in db.scalars(select(EvidenceReference).where(EvidenceReference.event_id.in_(ids),
                                                            EvidenceReference.finding_id.is_(None))):
            ev_map[e.event_id].append(evidence(e))
    out = []
    for e in rows:
        status = "human_verified" if e.source_status == "human_verified" else (
            "uncertain" if e.source_status == "uncertain" or e.timestamp_basis == "page_context_date" else "extracted")
        out.append({"id": e.id, "case_id": e.case_id, "occurred_at": iso(e.occurred_at),
                    "occurred_at_precision": e.occurred_at_precision if e.occurred_at else "unknown",
                    "timestamp_basis": e.timestamp_basis, "raw_time_text": e.raw_time_text,
                    "type": e.type, "subtype": e.subtype, "title": e.title, "description": e.description,
                    "severity": e.severity, "evidence": ev_map.get(e.id, []), "source_status": status,
                    "verification_status": e.verification_status, "flags": e.flags or [],
                    "source_document_id": e.document_id, "source_page": e.page_number})
    return out


def vital(v: VitalObservation) -> dict[str, Any]:
    return {"id": v.id, "case_id": v.case_id, "observed_at": iso(v.observed_at), "precision": v.precision,
            "timestamp_basis": v.timestamp_basis,
            "measurement": {"type": v.measurement_type, "value": v.value, "unit": v.unit, "original_unit": v.original_unit,
                            "value_text": v.value_text},
            "evidence": source_evidence(v), "source_status": v.source_status}


def medication(m: MedicationRecord) -> dict[str, Any]:
    return {"id": m.id, "case_id": m.case_id, "drug_name": m.drug_name,
            "dose": {"value": m.dose_value, "unit": m.dose_unit}, "route": m.route, "frequency": m.frequency,
            "record_kind": m.record_kind, "ordered_at": iso(m.ordered_at), "ordered_precision": m.ordered_precision,
            "administered_at": iso(m.administered_at), "administered_precision": m.administered_precision,
            "uncertainty": m.uncertainty or [], "evidence": source_evidence(m), "source_status": m.source_status}


def laboratory(l: LaboratoryRecord) -> dict[str, Any]:
    return {"id": l.id, "case_id": l.case_id, "test_name": l.test_name, "test_code": l.test_code,
            "value": {"numeric": l.value_num, "text": l.value_text, "unit": l.unit},
            "reference_range": {"text": l.reference_range_text, "low": l.ref_low, "high": l.ref_high},
            "abnormal_flag": l.abnormal_flag, "abnormal_basis": l.abnormal_basis,
            "sampled_at": iso(l.sampled_at), "sampled_precision": l.sampled_precision,
            "resulted_at": iso(l.resulted_at), "resulted_precision": l.resulted_precision,
            "evidence": source_evidence(l), "source_status": l.source_status}


def recommendations(db: Session, rows: list[Recommendation]) -> list[dict[str, Any]]:
    cm = comments_for(db, "recommendation", [r.id for r in rows])
    return [{"id": r.id, "case_id": r.case_id, "finding_id": r.finding_id, "category": r.category,
             "description": r.description, "priority": r.priority, "suggested_owner": r.suggested_owner,
             "owner_id": r.owner_id, "due_date": r.due_date.isoformat() if r.due_date else None, "status": r.status,
             "source": r.source, "protocol_ref": r.protocol_ref, "supporting_evidence": r.supporting_evidence or [],
             "tracked_as_action": r.tracked_as_action, "reviewer_comments": cm.get(r.id, []),
             "completed_at": iso(r.completed_at), "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}
            for r in rows]


def assessment(pa) -> dict[str, Any] | None:
    if pa is None:
        return None
    return {"id": pa.id, "assessment_type": pa.assessment_type, "category": pa.category, "rationale": pa.rationale,
            "preliminary": pa.assessment_type == "preliminary", "framework_id": pa.framework_id,
            "framework_version": pa.framework_version, "score": pa.score,
            "score_is_probability": False, "score_breakdown": pa.score_breakdown or [], "inputs": pa.inputs or {},
            "contributing_factors": pa.contributing_factors or [], "uncertainties": pa.uncertainties or [],
            "missing_information": pa.missing_information or [], "supporting_finding_ids": pa.supporting_finding_ids or [],
            "created_by": pa.created_by, "created_at": iso(pa.created_at)}
