"""Clinical timeline reconstruction.

* Note/event rows from agents are kept as-is; vitals, labs and medications are projected into events.
* Only events with reliable timestamps are ordered by time; untimed events keep source order.
* Missing times are never invented. Conflicts and context-derived dates are flagged.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (ClinicalEvent, EvidenceReference, LaboratoryRecord, MedicationRecord, PatientCase,
                        VitalObservation)

TIMELINE_AGENT = "clinical_timeline"
VITAL_LABELS = {"heart_rate": "HR", "bp_systolic": "SBP", "bp_diastolic": "DBP", "spo2": "SpO2",
                "temperature": "Temp", "respiratory_rate": "RR", "mean_arterial_pressure": "MAP", "gcs": "GCS",
                "capillary_glucose": "CBG"}


def _fmt(v: float | None) -> str:
    return "?" if v is None else (str(int(v)) if float(v).is_integer() else f"{v:g}")


def build_timeline(db: Session, case: PatientCase, job_id: str | None) -> dict:
    db.execute(delete(ClinicalEvent).where(ClinicalEvent.case_id == case.id, ClinicalEvent.agent_name == TIMELINE_AGENT))
    db.flush()

    # Vitals: one event per charted row (same page/line/time).
    groups: dict[tuple, list[VitalObservation]] = defaultdict(list)
    for v in db.scalars(select(VitalObservation).where(VitalObservation.case_id == case.id)):
        groups[(v.document_id, v.page_number, v.source_sequence, v.observed_at)].append(v)
    for (doc, page, seq, at), vs in groups.items():
        parts = [f"{VITAL_LABELS.get(v.measurement_type, v.measurement_type)} {_fmt(v.value)} {v.unit or ''}".strip()
                 for v in sorted(vs, key=lambda x: x.measurement_type)]
        ev = ClinicalEvent(case_id=case.id, occurred_at=at, occurred_at_precision=vs[0].precision,
                           timestamp_basis=vs[0].timestamp_basis, type="vital", subtype="observation_set",
                           title="Vital signs recorded", description=" · ".join(parts), agent_name=TIMELINE_AGENT,
                           document_id=doc, page_number=page, excerpt=vs[0].excerpt, source_sequence=seq,
                           source_status="uncertain" if any(v.source_status == "uncertain" for v in vs) else "extracted",
                           flags=["date_from_page_context"] if vs[0].timestamp_basis == "page_context_date" else [],
                           job_id=job_id)
        db.add(ev)
        db.flush()
        for v in vs:
            v.event_id = ev.id

    # Laboratory: one event per page/result time.
    lab_groups: dict[tuple, list[LaboratoryRecord]] = defaultdict(list)
    for lab in db.scalars(select(LaboratoryRecord).where(LaboratoryRecord.case_id == case.id)):
        lab_groups[(lab.document_id, lab.page_number, lab.resulted_at or lab.sampled_at)].append(lab)
    for (doc, page, at), labs in lab_groups.items():
        parts = [f"{l.test_name} {l.value_text} {l.unit or ''}{' (' + l.abnormal_flag.upper() + ')' if l.abnormal_flag else ''}".strip()
                 for l in labs]
        resulted = labs[0].resulted_at is not None
        db.add(ClinicalEvent(case_id=case.id, occurred_at=at,
                             occurred_at_precision=(labs[0].resulted_precision if resulted else labs[0].sampled_precision),
                             timestamp_basis="explicit" if at else "none", type="laboratory",
                             subtype="result_reported" if resulted else "sample_collected",
                             title="Laboratory results reported" if resulted else "Laboratory sample documented",
                             description="; ".join(parts)[:600], agent_name=TIMELINE_AGENT, document_id=doc,
                             page_number=page, excerpt="\n".join(l.excerpt or "" for l in labs[:5])[:500],
                             source_sequence=min(l.source_sequence for l in labs),
                             severity=None, flags=[] if at else ["missing_timestamp"], job_id=job_id))

    # Medication records.
    for m in db.scalars(select(MedicationRecord).where(MedicationRecord.case_id == case.id)):
        at = m.administered_at or m.ordered_at
        verb = {"administration": "administered", "order": "ordered", "change": "changed"}.get(m.record_kind, "documented")
        dose = f" {_fmt(m.dose_value)} {m.dose_unit}" if m.dose_value is not None else ""
        db.add(ClinicalEvent(case_id=case.id, occurred_at=at,
                             occurred_at_precision=m.administered_precision if m.administered_at else m.ordered_precision,
                             timestamp_basis="explicit" if at else "none", type="medication", subtype=m.record_kind,
                             title=f"Medication {verb}: {m.drug_name}",
                             description=f"{m.drug_name}{dose} {m.route or ''} {m.frequency or ''}".strip(),
                             agent_name=TIMELINE_AGENT, document_id=m.document_id, page_number=m.page_number,
                             excerpt=m.excerpt, source_sequence=m.source_sequence,
                             source_status=m.source_status, flags=[] if at else ["missing_timestamp"], job_id=job_id))
    db.flush()

    events = db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == case.id)).all()
    flagged = 0
    window_start = case.admitted_at - timedelta(days=1) if case.admitted_at else None
    window_end = case.outcome_at + timedelta(hours=6) if case.outcome_at else None
    for ev in events:
        flags = set(ev.flags or [])
        if ev.occurred_at is not None:
            if (window_start and ev.occurred_at < window_start) or (window_end and ev.occurred_at > window_end):
                flags.add("outside_admission_window")
        if flags != set(ev.flags or []):
            ev.flags = sorted(flags)
            flagged += 1

    # Evidence references for every event (page-level provenance).
    db.execute(delete(EvidenceReference).where(EvidenceReference.case_id == case.id, EvidenceReference.event_id.is_not(None)))
    for ev in events:
        db.add(EvidenceReference(case_id=case.id, event_id=ev.id, document_id=ev.document_id, page_number=ev.page_number,
                                 excerpt=ev.excerpt, event_at=ev.occurred_at, verification_status="unverified"))
    db.flush()
    timed = sorted((e for e in events if e.occurred_at), key=lambda e: (e.occurred_at, e.source_sequence))
    return {"events": len(events), "timed": len(timed), "untimed": len(events) - len(timed), "flagged": flagged,
            "first_event_at": timed[0].occurred_at.isoformat() if timed else None,
            "last_event_at": timed[-1].occurred_at.isoformat() if timed else None}


def ordered_events(db: Session, case_id: str) -> list[ClinicalEvent]:
    """Timed events chronologically, then untimed events in original source order."""
    events = db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == case_id)).all()
    timed = sorted((e for e in events if e.occurred_at), key=lambda e: (e.occurred_at, e.source_sequence))
    untimed = sorted((e for e in events if not e.occurred_at), key=lambda e: e.source_sequence)
    return timed + untimed
