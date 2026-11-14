"""Case-scoped AI assistant. Retrieves validated structured data (never whole documents), composes an
evidence-cited answer deterministically, and optionally asks the local LLM to phrase it from those
facts only. Documented facts and interpretations are kept distinct; clinical advice is declined."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_engine.ollama.client import LLMResponseInvalid, LLMUnavailable, get_ollama
from app.ai_engine.prompts.templates import narrative_prompt
from app.ai_engine.validators.llm_output import screen_narrative
from app.audit_engine.timeline.builder import ordered_events
from app.models import AuditFinding, EvidenceReference, LaboratoryRecord, MedicationRecord, PatientCase, Recommendation
from app.services import serializers as S
from app.utils.ids import iso

ADVICE = re.compile(r"\b(should\s+(?:i|we)\s+(?:give|prescribe|start|stop|treat)|what\s+(?:dose|treatment|drug)\s+should|"
                    r"recommend\s+(?:a\s+)?(?:dose|treatment|drug)|how\s+(?:do|should)\s+(?:i|we)\s+treat|"
                    r"was\s+(?:this|the\s+death)\s+(?:preventable|negligen\w*)|who\s+is\s+(?:to\s+blame|responsible|at\s+fault))\b", re.I)
FINDING_ID = re.compile(r"\bFND-[0-9A-F]{12}\b", re.I)
INTENTS = [
    ("evidence", re.compile(r"\bevidence|support(?:s|ing)?\b|\bsource\b", re.I)),
    ("explain_finding", re.compile(r"\bexplain|\bwhy\b.*\bflag|\bfinding\b", re.I)),
    ("documentation_gaps", re.compile(r"\bdocumentation|\bgaps?\b|\bmissing\b|\bsignature|\btimestamp", re.I)),
    ("recommendations", re.compile(r"\brecommend|\bactions?\b|\boutstanding|\bcorrective", re.I)),
    ("medications", re.compile(r"\bmedication|\bdrugs?\b|\bantibiotic|\bmed(?:s|ication)? records?|\bdose", re.I)),
    ("laboratory", re.compile(r"\blab(?:oratory)?\b|\blactate|\bcreatinine|\bresults?\b|\btests?\b", re.I)),
    ("timeline", re.compile(r"\btimeline|\bevents?\b|\bwhen\b|\bhappened|\bcpr\b|\bicu\b|\bescalat|\bvitals?\b", re.I)),
    ("summary", re.compile(r"\bsummar|\bclinical course|\boverview|\bwhat happened", re.I)),
]
TIMELINE_KEYWORDS = {"cpr": ["cpr"], "icu": ["icu_escalation"], "escalat": ["icu_escalation"], "vital": ["vital"],
                     "lab": ["laboratory"], "medication": ["medication"], "nurs": ["nursing_note"], "doctor": ["doctor_note"],
                     "deteriorat": ["emergency"], "procedure": ["procedure"]}


@dataclass
class Answer:
    intent: str
    facts: list[str]
    evidence: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    interpretations: list[str] = field(default_factory=list)


def classify_intent(message: str) -> str:
    if ADVICE.search(message):
        return "declined_advice"
    if FINDING_ID.search(message):
        return "evidence" if re.search(r"evidence|support|source", message, re.I) else "explain_finding"
    for name, pat in INTENTS:
        if pat.search(message):
            return name
    return "help"


def _ev(e: dict) -> dict:
    return {k: e.get(k) for k in ("document_id", "page_number", "region", "excerpt", "event_at", "verification_status",
                                  "extraction_confidence", "confidence_method")}


def _event_line(e: dict) -> str:
    when = e["occurred_at"] or f"time not documented{' (written: ' + e['raw_time_text'] + ')' if e.get('raw_time_text') else ''}"
    return f"{when} — {e['title']}: {e['description'][:160]} [doc {e['source_document_id']}, p.{e['source_page']}]"


def answer(db: Session, case: PatientCase, message: str) -> Answer:
    intent = classify_intent(message)
    base_limits = ["Answers use extracted, structured case data; verify against the original pages before relying on them."]
    if intent == "declined_advice":
        return Answer(intent, ["I can't provide treatment advice or determine preventability, negligence or blame. "
                               "I can summarise what is documented and point to the evidence for committee review."],
                      limitations=["Clinical decisions and preventability determinations belong to qualified reviewers."])

    findings_rows = db.scalars(select(AuditFinding).where(AuditFinding.case_id == case.id, AuditFinding.superseded.is_(False))).all()
    if intent in ("explain_finding", "evidence"):
        m = FINDING_ID.search(message)
        target = [f for f in findings_rows if m and f.id.upper() == m.group(0).upper()] or findings_rows[:1]
        if not target:
            return Answer(intent, ["No findings exist for this case."], limitations=base_limits)
        f = S.findings(db, target)[0]
        facts = [f"Finding {f['id']} ({f['category']}, severity {f['severity']}, review status {f['review_status']}): {f['title']}.",
                 f"Rule: {f['rule']['rule_id']} from {f['rule']['source']} version {f['rule']['protocol_version']}.",
                 f"Description: {f['description']}",
                 f"Evidence verification: {f['verification_status']}; {len(f['evidence'])} evidence reference(s)."]
        facts += [f"Evidence p.{e['page_number']} ({e['verification_status']}): \"{(e['excerpt'] or '')[:160]}\"" for e in f["evidence"][:6]]
        return Answer(intent, facts, [_ev(e) for e in f["evidence"][:10]],
                      base_limits + [u for u in f["uncertainty"]] + ["This finding is preliminary until a reviewer accepts it."])

    if intent == "documentation_gaps":
        rows = [f for f in findings_rows if f.category == "documentation_quality"]
        if not rows:
            return Answer(intent, ["No documentation-quality findings were generated for this case."], limitations=base_limits)
        fs = S.findings(db, rows)
        return Answer(intent, [f"{f['title']} — {f['description']} (review: {f['review_status']})" for f in fs],
                      [_ev(e) for f in fs for e in f["evidence"][:3]], base_limits + [u for f in fs for u in f["uncertainty"]][:6])

    if intent == "recommendations":
        recs = S.recommendations(db, db.scalars(select(Recommendation).where(
            Recommendation.case_id == case.id, Recommendation.status.in_(["open", "in_progress", "reopened"]))).all())
        if not recs:
            return Answer(intent, ["There are no outstanding recommendations for this case."], limitations=base_limits)
        return Answer(intent, [f"[{r['priority']}] {r['description']} — status {r['status']}, due {r['due_date'] or 'not set'}, "
                               f"owner {r['suggested_owner'] or 'unassigned'}" for r in recs], limitations=base_limits)

    if intent == "medications":
        meds = db.scalars(select(MedicationRecord).where(MedicationRecord.case_id == case.id)
                          .order_by(MedicationRecord.source_sequence)).all()
        if not meds:
            return Answer(intent, ["No medication entries were extracted from this record."],
                          limitations=base_limits + ["Medication charts may be handwritten and not captured."])
        facts, ev = [], []
        for m in meds[:30]:
            when = iso(m.administered_at or m.ordered_at) or "time not documented"
            facts.append(f"{m.record_kind}: {m.drug_name} {m.dose_value or ''} {m.dose_unit or ''} {m.route or ''} "
                         f"{m.frequency or ''} — {when} [p.{m.page_number}]".replace("  ", " "))
            ev += [_ev(x) for x in S.source_evidence(m)]
        return Answer(intent, facts, ev[:15], base_limits + ["Absence of an administration entry is not evidence that a dose was missed."])

    if intent == "laboratory":
        labs = db.scalars(select(LaboratoryRecord).where(LaboratoryRecord.case_id == case.id)
                          .order_by(LaboratoryRecord.source_sequence)).all()
        if not labs:
            return Answer(intent, ["No laboratory results were extracted from this record."], limitations=base_limits)
        facts = [f"{l.test_name}: {l.value_text} {l.unit or ''} (ref {l.reference_range_text or 'not documented'})"
                 f"{' flagged ' + l.abnormal_flag + ' (' + (l.abnormal_basis or '') + ')' if l.abnormal_flag else ''}"
                 f" — resulted {iso(l.resulted_at) or 'time not documented'} [p.{l.page_number}]" for l in labs[:30]]
        return Answer(intent, facts, [_ev(x) for l in labs[:15] for x in S.source_evidence(l)], base_limits)

    events = S.events(db, ordered_events(db, case.id))
    if intent == "timeline":
        types = {t for k, ts in TIMELINE_KEYWORDS.items() if k in message.lower() for t in ts}
        sel = [e for e in events if not types or e["type"] in types]
        if not types:
            sel = [e for e in sel if e["type"] != "vital"]
        if not sel:
            return Answer(intent, ["No matching timeline events were found."], limitations=base_limits)
        return Answer(intent, [_event_line(e) for e in sel[:25]], [_ev(x) for e in sel[:10] for x in e["evidence"][:1]],
                      base_limits + (["Some events have no documented time and are listed in source order."]
                                     if any(not e["occurred_at"] for e in sel) else []))

    if intent == "summary":
        key = [e for e in events if e["type"] in ("icu_escalation", "cpr", "emergency", "procedure", "doctor_note")
               or e["subtype"] == "death_declared"]
        facts = [f"Admission: {iso(case.admitted_at) or 'not documented'}; outcome: {iso(case.outcome_at) or 'not documented'}; "
                 f"department: {case.department or 'not documented'}.",
                 f"Recorded cause of death (as documented): {case.cause_as_recorded or 'not documented'}."]
        facts += [_event_line(e) for e in key[:15]]
        return Answer(intent, facts, [_ev(x) for e in key[:8] for x in e["evidence"][:1]],
                      base_limits + ["Summary of documented events only; it is not an assessment of care quality."])

    return Answer("help", ["I can: summarise the documented clinical course; list documentation gaps; explain a finding "
                           "(e.g. 'explain FND-…'); show the evidence for a finding; retrieve timeline events (e.g. 'ICU "
                           "escalation events'); summarise medication or laboratory records; list outstanding recommendations."],
                  limitations=base_limits)


def phrase(ans: Answer, message: str) -> tuple[str, bool]:
    """Deterministic text, optionally rephrased by the local LLM using only these facts."""
    fallback = "\n".join(f"• {f}" for f in ans.facts)
    if ans.intent in ("help", "declined_advice"):
        return fallback, False
    llm = get_ollama()
    try:
        if not llm.available():
            return fallback, False
        system, prompt, schema = narrative_prompt(json.dumps({"question": message[:500], "facts": ans.facts}),
                                                  "Answer the question in under 150 words using ONLY these documented facts. "
                                                  "Keep page references. If the facts do not answer it, say so.")
        text = str(llm.generate_json(system, prompt, schema).data.get("text", "")).strip()
    except (LLMUnavailable, LLMResponseInvalid, AttributeError):
        return fallback, False
    text, _ = screen_narrative(text)
    if len(text) < 20:
        return fallback, False
    return text + "\n\nDocumented facts used:\n" + fallback, True
