"""Rule-based clinical information extraction from page text.

Outputs only what is written. Values are tagged documented / normalized / uncertain;
fields that are expected but absent are reported as `missing` by the agents, never guessed.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from app.document_intelligence.extraction import patterns as P
from app.document_intelligence.extraction.timestamps import TimeMatch, parse_timestamp
from app.document_intelligence.validation.entities import (EventItem, LabItem, MedicationItem, PatientField,
                                                           SourceRef, Timestamped, VitalItem)


@dataclass
class PageText:
    document_id: str
    page_number: int
    page_type: str
    text: str
    ocr_confidence: float | None = None
    confidence_method: str = "unavailable"


@dataclass
class Line:
    index: int
    text: str
    ts: TimeMatch
    context_date: date | None


def iter_lines(page: PageText) -> Iterator[Line]:
    ctx: date | None = None
    for i, raw in enumerate(page.text.splitlines()):
        text = raw.strip()
        if not text:
            continue
        ts = parse_timestamp(text, ctx)
        if ts.has_date and ts.date_value:
            ctx = ts.date_value
        yield Line(i, text, ts, ctx)


def _src(page: PageText, line: Line, excerpt: str | None = None) -> SourceRef:
    return SourceRef(document_id=page.document_id, page_number=page.page_number,
                     excerpt=(excerpt or line.text)[:500], line_index=line.index)


def _time(ts: TimeMatch) -> dict:
    return {"at": ts.value, "precision": ts.precision, "basis": ts.basis, "raw_time": ts.raw}


def _status_for(ts: TimeMatch, base: str = "documented") -> str:
    return "uncertain" if ts.basis == "page_context_date" else base


# --- Patient fields --------------------------------------------------------------

_PRIORITY_TYPES = {"death_summary": 0, "discharge_summary": 1, "emergency": 2, "doctor_notes": 3}
GENDER_MAP = {"m": "male", "f": "female", "male": "male", "female": "female", "other": "other",
              "transgender": "transgender"}


def extract_patient_fields(pages: list[PageText]) -> list[PatientField]:
    """All candidate field values with sources; agents reconcile conflicts."""
    out: list[PatientField] = []
    ordered = sorted(pages, key=lambda p: (_PRIORITY_TYPES.get(p.page_type, 9), p.page_number))
    for page in ordered:
        for line in iter_lines(page):
            for field, pat in P.PATIENT_FIELDS.items():
                for m in pat.finditer(line.text):
                    val = (m.groupdict().get("v") or m.groupdict().get("v2") or "").strip(" .,;:-")
                    if not val:
                        continue
                    normalized = None
                    status = "documented"
                    if field == "age":
                        age = int(val)
                        if not 0 <= age <= 120:
                            status = "uncertain"
                        normalized = {"age_years": age}
                    elif field == "gender":
                        normalized = {"gender": GENDER_MAP.get(val.lower(), val.lower())}
                        if val.lower() in ("m", "f"):
                            status = "normalized"
                    elif field in ("admitted_at", "outcome_at"):
                        ts = parse_timestamp(val)
                        if ts.value is None:
                            status = "uncertain"
                        normalized = {"at": ts.value.isoformat() if ts.value else None, "precision": ts.precision,
                                      "raw": ts.raw}
                    out.append(PatientField(field=field, value=val[:250], normalized=normalized, status=status,
                                            source=_src(page, line)))
            for location, pat in P.DEATH_LOCATION:
                if pat.search(line.text):
                    out.append(PatientField(field="death_location", value=location, normalized={"death_location": location},
                                            status="documented", source=_src(page, line)))
    return out


# --- Vitals ----------------------------------------------------------------------

def _plausible(kind: str, v: float) -> bool:
    lo, hi = P.PLAUSIBLE.get(kind, (float("-inf"), float("inf")))
    return lo <= v <= hi


def extract_vitals(page: PageText) -> list[VitalItem]:
    items: list[VitalItem] = []
    for line in iter_lines(page):
        for kind, pat in P.VITAL_PATTERNS.items():
            for m in pat.finditer(line.text):
                base_status = _status_for(line.ts)
                if kind == "blood_pressure":
                    for sub, grp in (("bp_systolic", "s"), ("bp_diastolic", "d")):
                        v = float(m.group(grp))
                        items.append(VitalItem(measurement_type=sub, value=v, value_text=m.group(0), unit="mmHg",
                                               status=base_status if _plausible(sub, v) else "uncertain",
                                               source=_src(page, line), **_time(line.ts)))
                    continue
                v = float(m.group("v"))
                unit = P.VITAL_UNITS[kind]
                original_unit = (m.groupdict().get("u") or "").strip() or None
                status = base_status
                if kind == "temperature":
                    is_f = (original_unit or "").upper() == "F" or (original_unit is None and 90 <= v <= 110)
                    if is_f:
                        v = round((v - 32) * 5 / 9, 1)
                        status = "normalized" if original_unit else "uncertain"
                        original_unit = "°F"
                    elif original_unit:
                        original_unit = "°C"
                if not _plausible(kind, v):
                    status = "uncertain"
                items.append(VitalItem(measurement_type=kind, value=v, value_text=m.group(0), unit=unit,
                                       original_unit=original_unit, status=status, source=_src(page, line),
                                       **_time(line.ts)))
    return items


# --- Laboratory --------------------------------------------------------------------

def _parse_ref(ref: str | None) -> tuple[float | None, float | None]:
    if not ref:
        return None, None
    ref = ref.replace("–", "-").replace(" to ", "-").replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$", ref)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^<(\d+(?:\.\d+)?)$", ref)
    if m:
        return None, float(m.group(1))
    m = re.match(r"^>(\d+(?:\.\d+)?)$", ref)
    if m:
        return float(m.group(1)), None
    return None, None


def extract_labs(page: PageText) -> list[LabItem]:
    items: list[LabItem] = []
    sampled = TimeMatch(None, "unknown", "none", None, False, False)
    resulted = TimeMatch(None, "unknown", "none", None, False, False)
    for line in iter_lines(page):
        sm = P.SAMPLE_TIME.search(line.text)
        if sm:
            sampled = parse_timestamp(sm.group("v"), line.context_date)
        rm = P.RESULT_TIME.search(line.text)
        if rm:
            resulted = parse_timestamp(rm.group("v"), line.context_date)
        if sm or rm:
            continue
        for m in P.LAB_LINE.finditer(line.text):
            name = m.group("name")
            code = P.ALIAS_TO_CODE.get(name.lower())
            if not code:
                continue
            # Single-letter / two-letter aliases need a unit or a separator to be trusted.
            if len(name) <= 2 and not (m.group("unit") or re.search(rf"{re.escape(name)}\s*[:=]", line.text, re.I)):
                continue
            raw_val = m.group("value").replace(" ", "")
            try:
                val = float(raw_val.lstrip("<>"))
            except ValueError:
                val = None
            ref_text = m.group("ref")
            lo, hi = _parse_ref(ref_text)
            flag_raw = (m.group("flag") or m.group("flag2") or "").upper()
            flag, basis, status = None, None, "documented"
            if flag_raw in ("H", "HIGH"):
                flag, basis = "high", "documented_flag"
            elif flag_raw in ("L", "LOW"):
                flag, basis = "low", "documented_flag"
            elif val is not None and (lo is not None or hi is not None) and not raw_val.startswith(("<", ">")):
                if hi is not None and val > hi:
                    flag, basis, status = "high", "documented_reference_range", "normalized"
                elif lo is not None and val < lo:
                    flag, basis, status = "low", "documented_reference_range", "normalized"
            line_ts = line.ts if line.ts.has_time or line.ts.has_date else None
            s_ts = line_ts or sampled
            items.append(LabItem(test_name=name, test_code=code, value_num=val, value_text=raw_val,
                                 unit=(m.group("unit") or None), reference_range_text=ref_text, ref_low=lo, ref_high=hi,
                                 abnormal_flag=flag, abnormal_basis=basis,
                                 sampled=Timestamped(**_time(s_ts)), resulted=Timestamped(**_time(resulted)),
                                 status="uncertain" if s_ts.basis == "page_context_date" and status == "documented" else status,
                                 source=_src(page, line, m.group(0))))
    return items


# --- Medication ----------------------------------------------------------------------

def extract_medications(page: PageText) -> list[MedicationItem]:
    items: list[MedicationItem] = []
    for line in iter_lines(page):
        for m in P.MED_LINE.finditer(line.text):
            if not (m.group("form") or m.group("route") or m.group("freq")):
                continue  # too ambiguous without a dosage form, route or frequency
            name = re.sub(r"\s+", " ", m.group("name")).strip()
            first = name.split(" ")[0].lower()
            if name.lower() in P.MED_NOT_DRUGS or first in P.MED_NOT_DRUGS or P.ALIAS_TO_CODE.get(name.lower()):
                continue
            uncertainty: list[str] = []
            if P.MED_ADMIN.search(line.text):
                kind = "administration"
            elif P.MED_CHANGE.search(line.text):
                kind = "change"
            elif P.MED_ORDER.search(line.text) or page.page_type == "prescription":
                kind = "order"
            else:
                kind = "unspecified"
            if line.ts.value is None and line.ts.raw is None:
                uncertainty.append("No time documented on this line")
            elif line.ts.basis == "page_context_date":
                uncertainty.append("Date taken from an earlier line on the same page")
            if page.ocr_confidence is not None and page.ocr_confidence < 60:
                uncertainty.append("Low OCR confidence on source page")
            items.append(MedicationItem(
                drug_name=(f"{m.group('form').capitalize()}. " if m.group("form") else "") + name,
                dose_value=float(m.group("dose")), dose_unit=m.group("unit").lower(),
                route=(m.group("route") or "").upper().replace(".", "") or None,
                frequency=(m.group("freq") or "").upper() or None, record_kind=kind,
                time=Timestamped(**_time(line.ts)), uncertainty=uncertainty,
                status="uncertain" if uncertainty and line.ts.basis == "page_context_date" else "documented",
                source=_src(page, line)))
    return items


# --- Events & note entries -------------------------------------------------------------

EVENT_TITLES = {"resuscitation": "Resuscitation / CPR documented", "death_declared": "Death declaration documented",
                "icu_transfer": "ICU transfer documented", "icu_request": "ICU / critical care request documented",
                "senior_informed": "Escalation to senior clinician documented",
                "deterioration": "Clinical deterioration documented", "procedure": "Procedure documented"}


def is_title_line(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and not any(c.isdigit() for c in text) and sum(c.isupper() for c in letters) / len(letters) > 0.7


def extract_events(page: PageText, agent: str) -> list[EventItem]:
    items: list[EventItem] = []
    for line in iter_lines(page):
        if is_title_line(line.text):
            continue
        for etype, subtype, pat in P.EVENT_PATTERNS:
            m = pat.search(line.text)
            if not m:
                continue
            items.append(EventItem(type=etype, subtype=subtype, title=EVENT_TITLES[subtype],
                                   description=line.text[:400], status=_status_for(line.ts),
                                   source=_src(page, line), agent=agent, **_time(line.ts)))
    return items


_UNTIMED_START = re.compile(r"^(?:seen\s+by\b|patient\s+(?:seen|reviewed)\b|ward\s+round\b|note\s*:)", re.I)
_ENTRY_START = re.compile(r"^\s*(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[01]?\d:[0-5]\d|[01]\d[0-5]\d\s*hrs?)")


def extract_note_entries(page: PageText, event_type: str, agent: str) -> list[EventItem]:
    """Split note pages into timestamped entries. Untimed blocks become entries with no timestamp."""
    entries: list[tuple[Line, list[str]]] = []
    current: tuple[Line, list[str]] | None = None
    lines = list(iter_lines(page))
    for line in lines:
        if _ENTRY_START.match(line.text) and (line.ts.has_time or line.ts.has_date):
            current = (line, [line.text])
            entries.append(current)
        elif _UNTIMED_START.match(line.text):
            untimed = Line(line.index, line.text, TimeMatch(None, "unknown", "none", None, False, False), line.context_date)
            current = (untimed, [line.text])
            entries.append(current)
        elif current is not None:
            current[1].append(line.text)
    if not entries and lines:
        # Page contains note text but no entry carries a time: one untimed entry, flagged.
        body = [ln.text for ln in lines[1:]] if len(lines) > 1 else [lines[0].text]
        entries = [(Line(lines[0].index, lines[0].text, TimeMatch(None, "unknown", "none", None, False, False), None), body)]
    label = "Doctor progress note" if event_type == "doctor_note" else "Nursing entry"
    out: list[EventItem] = []
    for head, body in entries:
        text = " ".join(body)
        out.append(EventItem(type=event_type, subtype="entry", title=label, description=text[:600],
                             status="uncertain" if head.ts.value is None else _status_for(head.ts),
                             has_signature_marker=bool(P.SIGNATURE.search(text)),
                             source=_src(page, head, text[:500]), agent=agent, **_time(head.ts)))
    return out
