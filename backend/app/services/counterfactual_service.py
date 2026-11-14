"""Exploratory, explicitly HYPOTHETICAL counterfactual analysis.

It never predicts outcomes. It re-positions a documented event on the timeline and lists the documented
events that would then fall after it, with the assumptions and limitations reviewers must weigh.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClinicalEvent, LaboratoryRecord, PatientCase
from app.utils.ids import iso

LABEL = "HYPOTHETICAL — exploratory analysis only. Not a clinical outcome, not proof of causation or preventability."
LIMITATIONS = [
    "No model of patient physiology or treatment response is used; outcomes cannot be inferred.",
    "Based solely on documented, extracted events; undocumented care is invisible to this analysis.",
    "Timestamps may reflect documentation time rather than the time care was delivered.",
]


def _ev(e: ClinicalEvent) -> dict:
    return {"event_id": e.id, "occurred_at": iso(e.occurred_at), "type": e.type, "subtype": e.subtype, "title": e.title,
            "document_id": e.document_id, "page_number": e.page_number, "excerpt": e.excerpt}


def analyse(db: Session, case: PatientCase, scenario_type: str, question: str, minutes_earlier: int | None) -> dict:
    events = sorted((e for e in db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == case.id)) if e.occurred_at),
                    key=lambda e: (e.occurred_at, e.source_sequence))
    assumptions: list[str] = []
    uncertainty: list[str] = []
    anchor = trigger = None
    if scenario_type in ("earlier_escalation", "earlier_icu_transfer"):
        trigger = next((e for e in events if e.subtype == "deterioration"), None)
        target_sub = "icu_transfer" if scenario_type == "earlier_icu_transfer" else None
        anchor = next((e for e in events if e.type == "icu_escalation" and (target_sub is None or e.subtype == target_sub)
                       and (trigger is None or e.occurred_at >= trigger.occurred_at)), None)
        if scenario_type == "earlier_icu_transfer":
            trigger = next((e for e in events if e.subtype == "icu_request"), trigger)
    elif scenario_type == "earlier_lab_review":
        lab = db.scalar(select(LaboratoryRecord).where(LaboratoryRecord.case_id == case.id,
                                                       LaboratoryRecord.abnormal_flag.is_not(None),
                                                       LaboratoryRecord.resulted_at.is_not(None))
                        .order_by(LaboratoryRecord.resulted_at))
        if lab is not None:
            trigger = next((e for e in events if e.type == "laboratory" and e.occurred_at == lab.resulted_at), None)
            anchor = next((e for e in events if e.type == "doctor_note" and e.occurred_at >= lab.resulted_at), None)

    if scenario_type == "custom" or anchor is None:
        if scenario_type != "custom":
            uncertainty.append("The documented record does not contain the events needed for this scenario")
        return {"label": LABEL, "hypothetical": True, "scenario": {"type": scenario_type, "question": question},
                "supporting_evidence": [], "timeline_events": [_ev(e) for e in events if e.type != "vital"][:30],
                "assumptions": ["Reviewers define the scenario; the system only retrieves documented events"],
                "limitations": LIMITATIONS, "uncertainty": uncertainty or ["Custom scenarios are not computed"],
                "reviewer_comments": []}

    actual_gap = (anchor.occurred_at - trigger.occurred_at) if trigger else None
    shift = timedelta(minutes=minutes_earlier) if minutes_earlier else (actual_gap or timedelta(0))
    hypothetical_time = anchor.occurred_at - shift
    if trigger and hypothetical_time < trigger.occurred_at:
        hypothetical_time = trigger.occurred_at
        assumptions.append("Hypothetical time capped at the triggering documented event")
    between = [e for e in events if hypothetical_time <= e.occurred_at < anchor.occurred_at and e.id != anchor.id]
    assumptions += [f"'{anchor.title}' is moved from {iso(anchor.occurred_at)} to {iso(hypothetical_time)}",
                    "All other documented events keep their recorded times"]
    if any(e.timestamp_basis != "explicit" for e in [anchor] + ([trigger] if trigger else [])):
        uncertainty.append("At least one anchor event's date was taken from page context, not written explicitly")
    return {
        "label": LABEL, "hypothetical": True,
        "scenario": {"type": scenario_type, "question": question, "minutes_earlier": int(shift.total_seconds() // 60),
                     "anchor_event": _ev(anchor), "trigger_event": _ev(trigger) if trigger else None,
                     "documented_interval_minutes": int(actual_gap.total_seconds() // 60) if actual_gap else None,
                     "hypothetical_anchor_time": iso(hypothetical_time)},
        "supporting_evidence": [{"document_id": e.document_id, "page_number": e.page_number, "excerpt": e.excerpt}
                                for e in ([trigger] if trigger else []) + [anchor]],
        "timeline_events": [_ev(e) for e in between],
        "observation": (f"{len(between)} documented event(s) occurred in the interval that the hypothetical change would "
                        "precede. What effect, if any, this would have had cannot be determined by this system."),
        "assumptions": assumptions, "limitations": LIMITATIONS,
        "uncertainty": uncertainty + ["Effect on outcome is unknown and is not estimated"], "reviewer_comments": [],
    }
