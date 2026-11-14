"""Agent 11 — Final report generation from validated data and verified findings.

AI-generated preliminary content and human/committee conclusions are kept in separate sections.
Narrative text (optional, via local LLM) is generated only from structured facts and screened for
unsupported conclusions; a deterministic template is always available.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_engine.agents.base import Agent, AgentContext, AgentOutput
from app.ai_engine.ollama.client import LLMResponseInvalid, LLMUnavailable, OllamaClient
from app.ai_engine.prompts.templates import narrative_prompt
from app.ai_engine.validators.llm_output import screen_narrative
from app.audit_engine.timeline.builder import ordered_events
from app.models import (AgentExecution, AuditFinding, AuditReport, Document, LaboratoryRecord, MedicationRecord,
                        PatientCase, PreventabilityAssessment, Recommendation, Review, VitalObservation)
from app.services import serializers as S
from app.utils.ids import iso, new_id, utcnow

REPORT_SCHEMA_VERSION = "1.0"
DISCLAIMER = ("Decision-support output for a qualified mortality audit committee. Findings are preliminary until "
              "reviewed. This report does not establish negligence, causation or preventability.")


def _current(db: Session, case_id: str, kind: str):
    return db.scalar(select(PreventabilityAssessment).where(
        PreventabilityAssessment.case_id == case_id, PreventabilityAssessment.assessment_type == kind)
        .order_by(PreventabilityAssessment.created_at.desc()).limit(1))


def _deterministic_course(case: PatientCase, events: list[dict]) -> str:
    timed = [e for e in events if e["occurred_at"]]
    if not timed:
        return "No timed clinical events could be reconstructed from the extracted record."
    key = [e for e in timed if e["type"] in ("icu_escalation", "cpr", "emergency", "procedure") or e["subtype"] == "death_declared"]
    parts = [f"{len(timed)} timed events were reconstructed between {timed[0]['occurred_at']} and {timed[-1]['occurred_at']}."]
    for e in key[:8]:
        parts.append(f"{e['occurred_at']}: {e['title']} (page {e['source_page']}).")
    return " ".join(parts)


def _narrative(llm: OllamaClient | None, facts: dict, instruction: str, fallback: str) -> tuple[str, bool, list[str]]:
    if llm is None:
        return fallback, False, []
    system, prompt, schema = narrative_prompt(json.dumps(facts, default=str), instruction)
    try:
        res = llm.generate_json(system, prompt, schema)
        text = str(res.data.get("text", "")).strip()
    except (LLMUnavailable, LLMResponseInvalid, AttributeError):
        return fallback, False, ["llm_narrative_unavailable"]
    text, warnings = screen_narrative(text)
    if len(text) < 20:
        return fallback, False, warnings + ["llm_narrative_rejected"]
    return text[:3000], True, warnings


def build_report(db: Session, case: PatientCase, llm: OllamaClient | None = None) -> tuple[dict, bool, list[str]]:
    findings_rows = db.scalars(select(AuditFinding).where(AuditFinding.case_id == case.id, AuditFinding.superseded.is_(False))
                               .order_by(AuditFinding.severity, AuditFinding.created_at)).all()
    findings = S.findings(db, findings_rows)
    events = S.events(db, ordered_events(db, case.id))
    recs = S.recommendations(db, db.scalars(select(Recommendation).where(Recommendation.case_id == case.id)).all())
    vitals = db.scalars(select(VitalObservation).where(VitalObservation.case_id == case.id)).all()
    meds = db.scalars(select(MedicationRecord).where(MedicationRecord.case_id == case.id)).all()
    labs = db.scalars(select(LaboratoryRecord).where(LaboratoryRecord.case_id == case.id)).all()
    docs = db.scalars(select(Document).where(Document.case_id == case.id, Document.deleted_at.is_(None))).all()
    prelim = _current(db, case.id, "preliminary")
    clinical = _current(db, case.id, "clinical_review")
    committee = _current(db, case.id, "committee_decision")
    reviews = db.scalars(select(Review).where(Review.case_id == case.id)).all()
    agents = db.scalars(select(AgentExecution).where(AgentExecution.case_id == case.id)
                        .order_by(AgentExecution.created_at.desc())).all()

    by_cat: dict[str, list[dict]] = {}
    for f in findings:
        by_cat.setdefault(f["category"], []).append(f)
    verified = [f for f in findings if f["verification_status"] in ("verified", "partially_verified", "absence_based")]

    llm_used = False
    warnings: list[str] = []
    facts = {"admission": iso(case.admitted_at), "outcome": iso(case.outcome_at), "department": case.department,
             "cause_as_recorded": case.cause_as_recorded,
             "key_events": [{"id": e["id"], "at": e["occurred_at"], "title": e["title"], "detail": e["description"][:160]}
                            for e in events if e["occurred_at"] and e["type"] != "vital"][:40]}
    course, used, w = _narrative(llm, facts, "Summarise the documented clinical course chronologically in under 180 words.",
                                 _deterministic_course(case, events))
    llm_used |= used
    warnings += w

    vit_types = Counter(v.measurement_type for v in vitals)
    lab_abn = [l for l in labs if l.abnormal_flag]
    overview = (f"Case {case.id}: {case.age_years if case.age_years is not None else 'age not documented'}"
                f"{', ' + case.gender if case.gender else ''}; department {case.department or 'not documented'}. "
                f"Recorded cause of death: {case.cause_as_recorded or 'not documented'}. "
                f"{len(findings)} preliminary findings ({len(verified)} with verified evidence) require committee review.")
    blank = None
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "case": S.case(db, case),
        "summary": {
            "overview": overview,
            "clinical_course": course,
            "major_findings": [f["title"] for f in findings if f["severity"] in ("critical", "high", "moderate")][:10],
            "treatment_gaps": [f["title"] for f in by_cat.get("medical_management", [])][:10],
            "documentation_observations": [f["title"] for f in by_cat.get("documentation_quality", [])][:10],
            "recommendations": [r["description"] for r in recs][:10],
        },
        "assessment": {
            "category": prelim.category if prelim else "undetermined",
            "rationale": prelim.rationale if prelim else "No preliminary assessment available.",
            "preliminary": True, "framework_id": prelim.framework_id if prelim else blank,
            "framework_version": prelim.framework_version if prelim else blank, "confidence": None,
            "uncertainty": (prelim.uncertainties or []) + (prelim.missing_information or []) if prelim else [],
            "committee_status": case.review_status,
        },
        "findings": findings,
        "timeline": events,
        "recommendations": recs,
        "review_status": case.review_status,
        "sections": {
            "1_patient_and_case_overview": {"text": overview, "patient_ref": case.patient_ref},
            "2_admission_details": {"admitted_at": iso(case.admitted_at), "admitted_at_precision": case.admitted_at_precision,
                                    "department": case.department, "ward": case.ward, "icu_details": case.icu_details,
                                    "provenance": (case.field_provenance or {}).get("admitted_at")},
            "3_clinical_course": {"text": course, "generated_by": "local_llm_from_structured_facts" if used else "template",
                                  "provisional": True},
            "4_chronological_timeline": {"event_count": len(events),
                                         "untimed_events": sum(1 for e in events if not e["occurred_at"])},
            "5_vitals_analysis": {"observations": len(vitals), "by_type": dict(vit_types),
                                  "uncertain_values": sum(1 for v in vitals if v.source_status == "uncertain")},
            "6_medication_analysis": {"records": len(meds), "by_kind": dict(Counter(m.record_kind for m in meds)),
                                      "drugs": sorted({m.drug_name for m in meds})[:50]},
            "7_laboratory_findings": {"results": len(labs),
                                      "documented_abnormal": [{"test": l.test_name, "value": l.value_text, "unit": l.unit,
                                                               "flag": l.abnormal_flag, "basis": l.abnormal_basis,
                                                               "page": l.page_number} for l in lab_abn][:30]},
            "8_medical_management_assessment": by_cat.get("medical_management", []),
            "9_nursing_and_monitoring_assessment": by_cat.get("nursing_monitoring", []),
            "10_documentation_quality_assessment": by_cat.get("documentation_quality", []),
            "11_system_and_process_gaps": by_cat.get("system_process", []),
            "12_evidence_linked_findings": {"total": len(findings), "verified_evidence": len(verified)},
            "13_preliminary_preventability_assessment": S.assessment(prelim),
            "14_recommendations": recs,
            "15_reviewer_decisions": {
                "clinical_review": S.assessment(clinical),
                "reviews": [{"id": r.id, "title": r.title, "status": r.status, "decision": r.decision,
                             "decided_at": iso(r.decided_at)} for r in reviews],
                "finding_review_counts": dict(Counter(f["review_status"] for f in findings)),
            },
            # Kept strictly separate from AI output; populated only by an authorised committee decision.
            "16_final_committee_conclusion": S.assessment(committee) if committee else None,
        },
        "source_documents": [{"document_id": d.id, "filename": d.filename, "sha256": d.sha256, "page_count": d.page_count}
                             for d in docs],
        "provenance": {"agents": [{"agent": a.agent_name, "status": a.status, "llm_used": a.llm_used,
                                   "model": a.llm_model, "job_id": a.job_id} for a in agents[:20]],
                       "narrative_llm_used": llm_used, "warnings": warnings},
    }
    return report, llm_used, warnings


def store_report(db: Session, case: PatientCase, report: dict, *, job_id: str | None, user_id: str | None) -> AuditReport:
    version = (db.scalar(select(func.max(AuditReport.version)).where(AuditReport.case_id == case.id)) or 0) + 1
    rid = new_id("RPT")
    now = utcnow()
    report = {**report, "report_id": rid, "report_version": version, "generated_at": iso(now)}
    content = json.dumps(report, default=str, sort_keys=True)
    row = AuditReport(id=rid, case_id=case.id, version=version, generated_at=now, generated_by=user_id, job_id=job_id,
                      review_status=case.review_status, content=content,
                      content_sha256=hashlib.sha256(content.encode()).hexdigest())
    db.add(row)
    db.flush()
    return row


class ReportGenerationAgent(Agent):
    name = "report_generation"
    description = "Generates the structured, versioned and encrypted audit report."
    uses_llm = True

    def run(self, ctx: AgentContext) -> AgentOutput:
        report, used, warnings = build_report(ctx.db, ctx.case, ctx.llm)
        row = store_report(ctx.db, ctx.case, report, job_id=ctx.job.id, user_id=None)
        return AgentOutput(summary={"report_id": row.id, "version": row.version, "sha256": row.content_sha256},
                           warnings=warnings, llm_used=used)
