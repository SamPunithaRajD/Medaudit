"""Agents 1–8: patient information, clinical notes, vitals, medication, laboratory, emergency/CPR,
nursing and documentation analysis."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import delete, select

from app.ai_engine.agents.base import Agent, AgentContext, AgentOutput
from app.document_intelligence.extraction import patterns as P
from app.document_intelligence.extraction.extractor import (PageText, extract_events, extract_labs,
                                                            extract_medications, extract_note_entries,
                                                            extract_patient_fields, extract_vitals)
from app.document_intelligence.extraction.timestamps import parse_timestamp
from app.document_intelligence.validation.entities import EventItem
from app.models import (ClinicalEvent, DocumentPage, ExtractedEntity, LaboratoryRecord, MedicationRecord,
                        OCRResult, VitalObservation)


def _seq(page_number: int, line_index: int) -> int:
    return page_number * 10000 + line_index


def save_events(ctx: AgentContext, agent: str, items: list[EventItem]) -> int:
    ctx.db.execute(delete(ClinicalEvent).where(ClinicalEvent.case_id == ctx.case.id, ClinicalEvent.agent_name == agent))
    seen = set()
    n = 0
    for it in items:
        key = (it.type, it.subtype, it.source.page_number, it.source.line_index)
        if key in seen:
            continue
        seen.add(key)
        flags = []
        if it.basis == "page_context_date":
            flags.append("date_from_page_context")
        if it.at is None:
            flags.append("missing_timestamp")
        ctx.db.add(ClinicalEvent(
            case_id=ctx.case.id, occurred_at=it.at, occurred_at_precision=it.precision, timestamp_basis=it.basis,
            raw_time_text=it.raw_time, type=it.type, subtype=it.subtype, title=it.title, description=it.description,
            source_status="uncertain" if it.status == "uncertain" else "extracted", agent_name=agent,
            document_id=it.source.document_id, page_number=it.source.page_number, excerpt=it.source.excerpt,
            source_sequence=_seq(it.source.page_number, it.source.line_index), flags=flags, job_id=ctx.job.id))
        n += 1
    ctx.db.flush()
    return n


def save_entity(ctx: AgentContext, agent: str, entity_type: str, field: str, value: str | None, *, status: str,
                page: PageText | None = None, document_id: str | None = None, page_number: int | None = None,
                excerpt: str | None = None, normalized: dict | None = None, method: str = "rule") -> None:
    ctx.db.add(ExtractedEntity(
        case_id=ctx.case.id, agent_name=agent, entity_type=entity_type, field_name=field, value_text=value,
        normalized_value=normalized, status=status, extraction_method=method,
        document_id=document_id or (page.document_id if page else None),
        page_number=page_number or (page.page_number if page else None), excerpt=excerpt,
        source_status="uncertain" if status == "uncertain" else "extracted", job_id=ctx.job.id))


def _clear_entities(ctx: AgentContext, agent: str) -> None:
    ctx.db.execute(delete(ExtractedEntity).where(ExtractedEntity.case_id == ctx.case.id,
                                                 ExtractedEntity.agent_name == agent))


# 1 ---------------------------------------------------------------------------------
class PatientInformationAgent(Agent):
    name = "patient_information"
    description = "Extracts demographics, admission/outcome details, diagnoses, complaints and comorbidities."
    uses_llm = True
    REQUIRED = ("age", "gender", "admitted_at", "outcome_at", "cause_as_recorded", "department")
    LIST_FIELDS = ("diagnosis", "presenting_complaint", "comorbidity")

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        _clear_entities(ctx, self.name)
        candidates = extract_patient_fields(ctx.pages)
        by_field: dict[str, list] = defaultdict(list)
        for c in candidates:
            by_field[c.field].append(c)

        # LLM assistance only for list fields that rules did not find, from summary/admission pages.
        summary_pages = ctx.pages_of("death_summary", "discharge_summary", "emergency", "doctor_notes")[:3]
        for field in self.LIST_FIELDS:
            if by_field.get(field) or not summary_pages:
                continue
            for page in summary_pages:
                for item in self.llm_items(ctx, page, f"List each explicitly documented {field.replace('_', ' ')}.",
                                           {"value": f"the {field.replace('_', ' ')} as written"}, out):
                    if item.get("value"):
                        save_entity(ctx, self.name, field, field, item["value"], status="documented", page=page,
                                    excerpt=item["evidence"], method="llm")

        resolved: dict[str, dict] = {}
        for field, cands in by_field.items():
            if field in self.LIST_FIELDS:
                seen = set()
                for c in cands:
                    if c.value.lower() in seen:
                        continue
                    seen.add(c.value.lower())
                    save_entity(ctx, self.name, field, field, c.value, status=c.status, document_id=c.source.document_id,
                                page_number=c.source.page_number, excerpt=c.source.excerpt, normalized=c.normalized)
                continue
            distinct = {(c.normalized or {}).get("at") or (c.normalized or {}).get(field) or c.value.lower() for c in cands}
            first = cands[0]
            status = first.status
            if len(distinct) > 1:
                status = "uncertain"
                out.warnings.append(f"conflicting_values:{field}")
            save_entity(ctx, "patient_information", "patient_info", field, first.value, status=status,
                        document_id=first.source.document_id, page_number=first.source.page_number,
                        excerpt=first.source.excerpt,
                        normalized={**(first.normalized or {}), "candidates": len(cands), "distinct_values": len(distinct)})
            resolved[field] = {"value": first.value, "normalized": first.normalized, "status": status,
                               "page": first.source.page_number, "document_id": first.source.document_id,
                               "conflict": len(distinct) > 1}
        for field in self.REQUIRED:
            if field not in resolved:
                save_entity(ctx, self.name, "patient_info", field, None, status="missing")
        self._apply_to_case(ctx, resolved)
        ctx.db.flush()
        out.summary = {"fields_found": sorted(resolved), "fields_missing": [f for f in self.REQUIRED if f not in resolved],
                       "conflicts": [f for f, v in resolved.items() if v["conflict"]]}
        return out

    @staticmethod
    def _apply_to_case(ctx: AgentContext, resolved: dict[str, dict]) -> None:
        """Fill case fields from documented values only when not already set by a human."""
        case = ctx.case
        prov = dict(case.field_provenance or {})

        def settable(field: str) -> bool:
            return prov.get(field, {}).get("source") not in ("human", "uploader")

        def mark(field: str, info: dict) -> None:
            prov[field] = {"source": "extracted", "document_id": info["document_id"], "page": info["page"],
                           "status": info["status"]}

        mapping = {"patient_name": "patient_name", "patient_identifier": "patient_identifier",
                   "department": "department", "ward": "ward", "cause_as_recorded": "cause_as_recorded",
                   "icu_details": "icu_details"}
        for field, attr in mapping.items():
            if field in resolved and settable(field):
                setattr(case, attr, resolved[field]["value"])
                mark(field, resolved[field])
        if "age" in resolved and settable("age") and resolved["age"]["status"] != "uncertain":
            case.age_years = (resolved["age"]["normalized"] or {}).get("age_years")
            mark("age", resolved["age"])
        if "gender" in resolved and settable("gender"):
            case.gender = (resolved["gender"]["normalized"] or {}).get("gender")
            mark("gender", resolved["gender"])
        for field in ("admitted_at", "outcome_at"):
            if field in resolved and settable(field):
                norm = resolved[field]["normalized"] or {}
                if norm.get("at"):
                    setattr(case, field, datetime.fromisoformat(norm["at"]))
                    setattr(case, f"{field}_precision", norm.get("precision", "unknown"))
                    mark(field, resolved[field])
        if "death_location" in resolved and settable("death_location"):
            case.death_location = resolved["death_location"]["value"]
            mark("death_location", resolved["death_location"])
        case.field_provenance = prov


# 2 ---------------------------------------------------------------------------------
class ClinicalNotesAgent(Agent):
    name = "clinical_notes"
    description = "Splits doctor progress notes into timed entries and records documented clinical decisions."
    page_types = ("doctor_notes", "icu", "emergency")
    uses_llm = True

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        _clear_entities(ctx, self.name)
        items: list[EventItem] = []
        decisions = 0
        for page in ctx.pages_of("doctor_notes"):
            items.extend(extract_note_entries(page, "doctor_note", self.name))
            for d in self.llm_items(ctx, page, "List each clinical decision or treatment plan explicitly written by a doctor.",
                                    {"decision": "the decision or plan as written", "time": "time written next to it, or null"}, out):
                save_entity(ctx, self.name, "clinical_decision", "decision", d.get("decision"), status="documented",
                            page=page, excerpt=d["evidence"], method="llm", normalized={"time_text": d.get("time")})
                decisions += 1
        n = save_events(ctx, self.name, items)
        untimed = sum(1 for i in items if i.at is None)
        out.summary = {"note_entries": n, "untimed_entries": untimed, "llm_decisions": decisions}
        return out


# 3 ---------------------------------------------------------------------------------
class VitalsAgent(Agent):
    name = "vitals"
    description = "Extracts vital observations, normalises units, flags implausible values and missing parameters."

    CORE = ("heart_rate", "bp_systolic", "spo2", "respiratory_rate", "temperature")

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        ctx.db.execute(delete(VitalObservation).where(VitalObservation.case_id == ctx.case.id))
        seen = set()
        sets: dict[tuple, set] = defaultdict(set)
        count = 0
        for page in ctx.pages:
            for v in extract_vitals(page):
                key = (v.measurement_type, v.at, v.value)
                if v.at is not None and key in seen:
                    continue  # same reading transcribed on several pages
                seen.add(key)
                ctx.db.add(VitalObservation(
                    case_id=ctx.case.id, observed_at=v.at, precision=v.precision, timestamp_basis=v.basis,
                    measurement_type=v.measurement_type, value=v.value, value_text=v.value_text, unit=v.unit,
                    original_unit=v.original_unit, document_id=v.source.document_id,
                    page_number=v.source.page_number, excerpt=v.source.excerpt,
                    source_status="uncertain" if v.status == "uncertain" else "extracted",
                    source_sequence=_seq(v.source.page_number, v.source.line_index), job_id=ctx.job.id))
                sets[(v.source.document_id, v.source.page_number, v.source.line_index, v.at)].add(v.measurement_type)
                count += 1
        ctx.db.flush()
        # Observation sets (one charted row) missing core parameters — reported, not judged.
        incomplete = [{"page": k[1], "missing": sorted(set(self.CORE) - present)}
                      for k, present in sets.items() if len(present) >= 2 and set(self.CORE) - present]
        types = Counter(m for s in sets.values() for m in s)
        ctx.shared["vital_sets_incomplete"] = incomplete
        out.summary = {"observations": count, "observation_sets": len(sets), "by_type": dict(types),
                       "untimed": sum(1 for k in sets if k[3] is None), "incomplete_sets": len(incomplete)}
        return out


# 4 ---------------------------------------------------------------------------------
class MedicationAgent(Agent):
    name = "medication"
    description = "Extracts medication orders, administrations and changes with timing; lists undocumented administrations."
    uses_llm = True

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        ctx.db.execute(delete(MedicationRecord).where(MedicationRecord.case_id == ctx.case.id))
        count = 0
        for page in ctx.pages:
            items = extract_medications(page)
            if not items and page.page_type in ("medication", "prescription"):
                for it in self.llm_items(ctx, page, "List each medication entry explicitly written.",
                                         {"drug_name": "drug name", "dose": "dose with unit or null", "route": "route or null",
                                          "frequency": "frequency or null", "time": "time written or null",
                                          "kind": "one of order, administration, change, unspecified"}, out):
                    ts = parse_timestamp(it.get("time") or "")
                    ctx.db.add(MedicationRecord(
                        case_id=ctx.case.id, drug_name=(it.get("drug_name") or "")[:200], route=it.get("route"),
                        frequency=it.get("frequency"),
                        record_kind=it.get("kind") if it.get("kind") in ("order", "administration", "change") else "unspecified",
                        ordered_at=ts.value if it.get("kind") == "order" else None,
                        administered_at=ts.value if it.get("kind") == "administration" else None,
                        uncertainty=["Extracted by language model; verify against the source page"],
                        document_id=page.document_id, page_number=page.page_number, excerpt=it["evidence"],
                        source_status="uncertain", job_id=ctx.job.id))
                    count += 1
                continue
            for m in items:
                is_admin = m.record_kind == "administration"
                ctx.db.add(MedicationRecord(
                    case_id=ctx.case.id, drug_name=m.drug_name, dose_value=m.dose_value, dose_unit=m.dose_unit,
                    route=m.route, frequency=m.frequency, record_kind=m.record_kind,
                    ordered_at=None if is_admin else m.time.at, ordered_precision="unknown" if is_admin else m.time.precision,
                    administered_at=m.time.at if is_admin else None,
                    administered_precision=m.time.precision if is_admin else "unknown",
                    uncertainty=m.uncertainty, document_id=m.source.document_id, page_number=m.source.page_number,
                    excerpt=m.source.excerpt, source_status="uncertain" if m.status == "uncertain" else "extracted",
                    source_sequence=_seq(m.source.page_number, m.source.line_index), job_id=ctx.job.id))
                count += 1
        ctx.db.flush()
        recs = ctx.db.scalars(select(MedicationRecord).where(MedicationRecord.case_id == ctx.case.id)).all()
        kinds = Counter(r.record_kind for r in recs)
        out.summary = {"records": count, "by_kind": dict(kinds), "drugs": len({drug_key(r.drug_name) for r in recs}),
                       "untimed": sum(1 for r in recs if r.ordered_at is None and r.administered_at is None)}
        return out


def drug_key(name: str) -> str:
    parts = [p for p in name.lower().replace(".", " ").split() if p not in ("inj", "tab", "cap", "inf", "syp", "neb", "susp")]
    return parts[0].split("-")[0] if parts else name.lower()


# 5 ---------------------------------------------------------------------------------
class LaboratoryAgent(Agent):
    name = "laboratory"
    description = "Extracts laboratory results with units, reference ranges, sample/result times and repeat tests."

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        ctx.db.execute(delete(LaboratoryRecord).where(LaboratoryRecord.case_id == ctx.case.id))
        count = 0
        for page in ctx.pages_of("laboratory", "icu", "doctor_notes", "emergency", "death_summary", "discharge_summary"):
            for lab in extract_labs(page):
                ctx.db.add(LaboratoryRecord(
                    case_id=ctx.case.id, test_name=lab.test_name, test_code=lab.test_code, value_num=lab.value_num,
                    value_text=lab.value_text, unit=lab.unit, reference_range_text=lab.reference_range_text,
                    ref_low=lab.ref_low, ref_high=lab.ref_high, abnormal_flag=lab.abnormal_flag,
                    abnormal_basis=lab.abnormal_basis, sampled_at=lab.sampled.at, sampled_precision=lab.sampled.precision,
                    resulted_at=lab.resulted.at, resulted_precision=lab.resulted.precision,
                    document_id=lab.source.document_id, page_number=lab.source.page_number, excerpt=lab.source.excerpt,
                    source_status="uncertain" if lab.status == "uncertain" else "extracted",
                    source_sequence=_seq(lab.source.page_number, lab.source.line_index), job_id=ctx.job.id))
                count += 1
        ctx.db.flush()
        recs = ctx.db.scalars(select(LaboratoryRecord).where(LaboratoryRecord.case_id == ctx.case.id)).all()
        repeats = {k: v for k, v in Counter(r.test_code for r in recs).items() if v > 1}
        out.summary = {"results": count, "abnormal_documented": sum(1 for r in recs if r.abnormal_flag),
                       "repeat_tests": repeats, "missing_units": sum(1 for r in recs if not r.unit),
                       "missing_result_time": sum(1 for r in recs if r.resulted_at is None)}
        return out


# 6 ---------------------------------------------------------------------------------
class EmergencyCPRAgent(Agent):
    name = "emergency_cpr"
    description = "Identifies deterioration, escalation, ICU transfer, CPR, procedures and death declaration."

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        items = [e for page in ctx.pages for e in extract_events(page, self.name)]
        n = save_events(ctx, self.name, items)
        out.summary = {"events": n, "by_subtype": dict(Counter(e.subtype for e in items)),
                       "cpr_documented": any(e.type == "cpr" for e in items)}
        return out


# 7 ---------------------------------------------------------------------------------
class NursingAgent(Agent):
    name = "nursing"
    description = "Splits nursing records into entries and summarises entries per shift."
    page_types = ("nursing_notes",)

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        items = [e for page in ctx.pages_of("nursing_notes") for e in extract_note_entries(page, "nursing_note", self.name)]
        n = save_events(ctx, self.name, items)
        shifts: Counter = Counter()
        for e in items:
            if e.at is not None:
                h = e.at.hour
                shifts[f"{e.at.date()}:{'morning' if 8 <= h < 14 else 'evening' if 14 <= h < 20 else 'night'}"] += 1
        out.summary = {"nursing_entries": n, "untimed_entries": sum(1 for e in items if e.at is None),
                       "entries_per_shift": dict(shifts)}
        return out


# 8 ---------------------------------------------------------------------------------
class DocumentationAgent(Agent):
    name = "documentation"
    description = ("Detects missing timestamps/signature markers, low-quality OCR, illegible pages, duplicate pages, "
                   "chronology inconsistencies and possible missing pages.")

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        _clear_entities(ctx, self.name)
        issues: Counter = Counter()

        def issue(kind: str, *, page: PageText | None = None, document_id=None, page_number=None, excerpt=None, **extra):
            save_entity(ctx, self.name, "documentation_issue", kind, extra.pop("value", None), status="documented",
                        page=page, document_id=document_id, page_number=page_number, excerpt=excerpt, normalized=extra)
            issues[kind] += 1

        # Note entries lacking times / signature markers (events already saved by agents 2 and 7).
        notes = ctx.db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == ctx.case.id,
                                                           ClinicalEvent.type.in_(["doctor_note", "nursing_note"]))).all()
        for ev in notes:
            if ev.occurred_at is None and ev.raw_time_text is None:
                issue("missing_timestamp", document_id=ev.document_id, page_number=ev.page_number,
                      excerpt=ev.excerpt, event_id=ev.id, note_type=ev.type)
            if not P.SIGNATURE.search(ev.description or ""):
                issue("missing_signature_marker", document_id=ev.document_id, page_number=ev.page_number,
                      excerpt=ev.excerpt, event_id=ev.id, note_type=ev.type)

        # Page-level quality from OCR results and page records.
        page_rows = ctx.db.execute(select(DocumentPage, OCRResult).join(
            OCRResult, (OCRResult.page_id == DocumentPage.id) & (OCRResult.is_primary.is_(True)), isouter=True)
            .where(DocumentPage.case_id == ctx.case.id)).all()
        declared_totals: dict[str, set[int]] = defaultdict(set)
        seen_numbers: dict[str, set[int]] = defaultdict(set)
        for pg, ocr in page_rows:
            if pg.duplicate_of_page is not None:
                issue("duplicate_page", document_id=pg.document_id, page_number=pg.page_number,
                      duplicate_of=pg.duplicate_of_page)
            if pg.status == "failed":
                issue("page_processing_failed", document_id=pg.document_id, page_number=pg.page_number)
            if ocr is not None:
                if ocr.handwriting_suspected:
                    issue("possible_handwriting_or_illegible", document_id=pg.document_id, page_number=pg.page_number,
                          mean_confidence=ocr.mean_confidence)
                elif ocr.mean_confidence is not None and ocr.mean_confidence < 60:
                    issue("low_ocr_confidence", document_id=pg.document_id, page_number=pg.page_number,
                          mean_confidence=ocr.mean_confidence)
                if not (ocr.text or "").strip():
                    issue("no_text_extracted", document_id=pg.document_id, page_number=pg.page_number)
                for m in P.PAGE_NUMBERING.finditer(ocr.text or ""):
                    declared_totals[pg.document_id].add(int(m.group("t")))
                    seen_numbers[pg.document_id].add(int(m.group("n")))

        # Possible missing pages: only when the record itself declares "Page n of N".
        for doc_id, totals in declared_totals.items():
            if len(totals) == 1:
                total = next(iter(totals))
                missing = sorted(set(range(1, total + 1)) - seen_numbers[doc_id])
                if missing and total <= 1000:
                    issue("possible_missing_pages", document_id=doc_id, value=",".join(map(str, missing[:50])),
                          declared_total=total, missing_count=len(missing))

        # Chronology: within a page, entries whose time runs backwards by more than an hour.
        events = ctx.db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == ctx.case.id,
                                                            ClinicalEvent.occurred_at.is_not(None))
                                .order_by(ClinicalEvent.source_sequence)).all()
        last: dict[tuple, ClinicalEvent] = {}
        for ev in events:
            key = (ev.document_id, ev.page_number, ev.agent_name)
            prev = last.get(key)
            if prev and (prev.occurred_at - ev.occurred_at).total_seconds() > 3600:
                issue("chronology_inconsistency", document_id=ev.document_id, page_number=ev.page_number,
                      excerpt=ev.excerpt, event_id=ev.id, previous_event_id=prev.id)
                ev.flags = list({*ev.flags, "chronology_inconsistent"})
            last[key] = ev
        ctx.db.flush()
        out.summary = {"issues": dict(issues)}
        return out
