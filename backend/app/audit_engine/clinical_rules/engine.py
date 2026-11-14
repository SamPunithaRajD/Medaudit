"""Deterministic rule evaluation against structured case data and approved protocol versions.

An LLM never decides whether a deviation exists. Findings are produced only by:
  * rules from approved, versioned hospital protocols, or
  * built-in documentation-integrity checks (no clinical thresholds involved).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_engine.agents.extraction_agents import drug_key
from app.audit_engine.clinical_rules.rules import (EscalationAfterAbnormal, EventInterval, LabReviewInterval,
                                                   MaxObservationInterval, MedicationAdministrationDocumented,
                                                   ProtocolRules, RequiredDocumentation)
from app.audit_engine.event_correlation.correlate import compare, first_after, gaps, minutes
from app.models import (AuditFinding, ClinicalEvent, DocumentPage, EvidenceReference, ExtractedEntity,
                        HospitalProtocol, LaboratoryRecord, MedicationRecord, PatientCase, ProtocolVersion,
                        VitalObservation)
from app.utils.ids import utcnow

BUILTIN_RULESET = "MEDAUDIT-DOC-INTEGRITY"
BUILTIN_VERSION = "1.0.0"


@dataclass
class Evidence:
    document_id: str | None
    page_number: int | None
    excerpt: str | None
    event_at: datetime | None = None
    event_id: str | None = None


@dataclass
class Candidate:
    rule_id: str
    category: str
    title: str
    description: str
    severity: str
    evidence: list[Evidence]
    rule_source: str
    protocol_id: str | None = None
    protocol_version: str | None = None
    occurred_at: datetime | None = None
    uncertainty: list[str] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)
    recommendation: dict | None = None
    key: str = ""

    def fingerprint(self) -> str:
        ev = "|".join(f"{e.document_id}:{e.page_number}:{(e.excerpt or '')[:60]}" for e in self.evidence[:10])
        return hashlib.sha256(f"{self.rule_id}|{self.protocol_version}|{self.key}|{ev}".encode()).hexdigest()


@dataclass
class ActiveRuleSet:
    protocol: HospitalProtocol
    version: ProtocolVersion
    rules: ProtocolRules


def active_rule_sets(db: Session, on: date | None = None, *, protocol_id: str | None = None,
                     version_id: str | None = None) -> tuple[list[ActiveRuleSet], list[str]]:
    """Latest approved & effective version of each protocol (or one explicit version for comparisons)."""
    on = on or utcnow().date()
    warnings: list[str] = []
    q = select(ProtocolVersion, HospitalProtocol).join(HospitalProtocol).where(HospitalProtocol.deleted_at.is_(None))
    if version_id:
        q = q.where(ProtocolVersion.id == version_id)
    else:
        q = q.where(ProtocolVersion.status == "approved")
        if protocol_id:
            q = q.where(HospitalProtocol.id == protocol_id)
    latest: dict[str, tuple[ProtocolVersion, HospitalProtocol]] = {}
    for ver, proto in db.execute(q).all():
        if not version_id and ver.effective_date and ver.effective_date > on:
            continue
        cur = latest.get(proto.id)
        if cur is None or (ver.approved_at or ver.created_at) > (cur[0].approved_at or cur[0].created_at):
            latest[proto.id] = (ver, proto)
    sets = []
    for ver, proto in latest.values():
        try:
            sets.append(ActiveRuleSet(proto, ver, ProtocolRules.model_validate(ver.rules or {})))
        except ValidationError:
            warnings.append(f"protocol_rules_invalid:{proto.id}:{ver.version}")
    return sets, warnings


def _ev(row, at_attr: str) -> Evidence:
    return Evidence(row.document_id, row.page_number, row.excerpt, getattr(row, at_attr, None),
                    getattr(row, "event_id", None) if not isinstance(row, ClinicalEvent) else row.id)


def _fmt_min(m: int) -> str:
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


class RuleEvaluator:
    def __init__(self, db: Session, case: PatientCase):
        self.db = db
        self.case = case
        cid = case.id
        self.vitals = db.scalars(select(VitalObservation).where(VitalObservation.case_id == cid,
                                                                VitalObservation.source_status != "uncertain")).all()
        self.events = db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == cid)).all()
        self.labs = db.scalars(select(LaboratoryRecord).where(LaboratoryRecord.case_id == cid)).all()
        self.meds = db.scalars(select(MedicationRecord).where(MedicationRecord.case_id == cid)).all()
        self.page_types = set(db.scalars(select(DocumentPage.page_type).where(DocumentPage.case_id == cid)).all())

    # --- protocol rules ---------------------------------------------------------
    def evaluate(self, rs: ActiveRuleSet) -> list[Candidate]:
        out: list[Candidate] = []
        for rule in rs.rules.rules:
            if not rule.enabled:
                continue
            fn = getattr(self, f"_rule_{rule.type}")
            for c in fn(rule):
                c.rule_source = "hospital_protocol"
                c.protocol_id, c.protocol_version = rs.protocol.id, rs.version.version
                c.recommendation = rule.recommendation.model_dump(mode="json") if rule.recommendation else None
                c.measurements.setdefault("rule", rule.model_dump(mode="json", exclude={"recommendation"}))
                out.append(c)
        return out

    def _base(self, rule, title, description, evidence, **kw) -> Candidate:
        return Candidate(rule_id=rule.rule_id, category=str(rule.category), title=title, description=description,
                         severity=str(rule.severity), evidence=evidence, rule_source="hospital_protocol", **kw)

    def _rule_max_observation_interval(self, rule: MaxObservationInterval) -> list[Candidate]:
        obs = sorted((v for v in self.vitals if v.measurement_type in rule.measurements and v.observed_at
                      and v.timestamp_basis == "explicit"), key=lambda v: v.observed_at)
        if rule.start_after_subtype:
            start = min((e.occurred_at for e in self.events if e.subtype == rule.start_after_subtype and e.occurred_at),
                        default=None)
            if start is None:
                return []
            obs = [v for v in obs if v.observed_at >= start]
        times, firsts = [], []
        for v in obs:
            if not times or v.observed_at != times[-1]:
                times.append(v.observed_at)
                firsts.append(v)
        out = []
        for i, j, d in gaps(times, timedelta(minutes=rule.max_minutes)):
            m = minutes(d)
            out.append(self._base(
                rule, rule.title,
                f"Documented observations of {', '.join(rule.measurements)} are {_fmt_min(m)} apart "
                f"(protocol maximum {_fmt_min(rule.max_minutes)}). Observations may exist on pages that were not "
                f"captured; verify against the original chart.",
                [_ev(firsts[i], "observed_at"), _ev(firsts[j], "observed_at")], occurred_at=times[i],
                measurements={"interval_minutes": m, "max_minutes": rule.max_minutes},
                uncertainty=["Absence of a documented observation does not prove it was not performed"],
                key=f"{times[i].isoformat()}"))
        return out

    def _rule_escalation_after_abnormal(self, rule: EscalationAfterAbnormal) -> list[Candidate]:
        readings = sorted((v for v in self.vitals if v.measurement_type == rule.measurement and v.observed_at
                           and v.timestamp_basis == "explicit"), key=lambda v: v.observed_at)
        esc_events = [e for e in self.events if e.type in rule.escalation_types and e.occurred_at]
        out, in_episode = [], False
        for v in readings:
            abnormal = compare(v.value, rule.operator, rule.value)
            if not abnormal:
                in_episode = False
                continue
            if in_episode:
                continue
            in_episode = True
            window = timedelta(minutes=rule.max_minutes)
            found = first_after(esc_events, v.observed_at, key=lambda e: e.occurred_at, within=window)
            if found is not None:
                continue
            later = first_after(esc_events, v.observed_at, key=lambda e: e.occurred_at)
            desc = (f"{rule.measurement} {v.value:g} recorded (protocol criterion {rule.operator} {rule.value:g}). "
                    f"No escalation/review entry of type {', '.join(rule.escalation_types)} is documented within "
                    f"{_fmt_min(rule.max_minutes)}.")
            ev = [_ev(v, "observed_at")]
            if later is not None:
                desc += f" The next documented entry is {_fmt_min(minutes(later.occurred_at - v.observed_at))} later."
                ev.append(_ev(later, "occurred_at"))
            out.append(self._base(rule, rule.title, desc, ev, occurred_at=v.observed_at,
                                  measurements={"value": v.value, "criterion": f"{rule.operator} {rule.value}",
                                                "next_escalation_minutes": minutes(later.occurred_at - v.observed_at) if later else None},
                                  uncertainty=["Verbal escalation may have occurred without documentation"],
                                  key=v.observed_at.isoformat()))
        return out

    def _rule_lab_review_interval(self, rule: LabReviewInterval) -> list[Candidate]:
        notes = [e for e in self.events if e.type == "doctor_note" and e.occurred_at]
        out = []
        for lab in self.labs:
            if rule.tests and lab.test_code not in rule.tests:
                continue
            if rule.abnormal_only and not lab.abnormal_flag:
                continue
            if lab.resulted_at is None:
                continue
            nxt = first_after(notes, lab.resulted_at, key=lambda e: e.occurred_at)
            if nxt is not None and nxt.occurred_at - lab.resulted_at <= timedelta(minutes=rule.max_minutes):
                continue
            desc = (f"{lab.test_name} {lab.value_text} {lab.unit or ''} reported; no physician note is documented within "
                    f"{_fmt_min(rule.max_minutes)} of the result time.")
            ev = [_ev(lab, "resulted_at")]
            if nxt is not None:
                desc += f" Next documented physician note: {_fmt_min(minutes(nxt.occurred_at - lab.resulted_at))} later."
                ev.append(_ev(nxt, "occurred_at"))
            out.append(self._base(rule, rule.title, desc, ev, occurred_at=lab.resulted_at,
                                  measurements={"test": lab.test_code, "max_minutes": rule.max_minutes},
                                  uncertainty=["A physician note may not mention every reviewed result"],
                                  key=f"{lab.test_code}:{lab.resulted_at.isoformat()}"))
        return out

    def _rule_event_interval(self, rule: EventInterval) -> list[Candidate]:
        starts = sorted((e for e in self.events if e.subtype == rule.from_subtype and e.occurred_at), key=lambda e: e.occurred_at)
        targets = [e for e in self.events if e.subtype == rule.to_subtype and e.occurred_at]
        out = []
        for s in starts[:1]:  # first request in the admission
            t = first_after(targets, s.occurred_at, key=lambda e: e.occurred_at)
            if t is None:
                if rule.flag_missing_target:
                    out.append(self._base(rule, rule.title,
                                          f"'{rule.from_subtype}' documented, but no subsequent '{rule.to_subtype}' is documented.",
                                          [_ev(s, "occurred_at")], occurred_at=s.occurred_at,
                                          uncertainty=["The subsequent event may be documented on an unprocessed page"],
                                          key=f"missing:{s.occurred_at.isoformat()}"))
                continue
            m = minutes(t.occurred_at - s.occurred_at)
            if m > rule.max_minutes:
                out.append(self._base(rule, rule.title,
                                      f"Interval from '{rule.from_subtype}' to '{rule.to_subtype}' is {_fmt_min(m)} "
                                      f"(protocol maximum {_fmt_min(rule.max_minutes)}).",
                                      [_ev(s, "occurred_at"), _ev(t, "occurred_at")], occurred_at=s.occurred_at,
                                      measurements={"interval_minutes": m, "max_minutes": rule.max_minutes},
                                      key=s.occurred_at.isoformat()))
        return out

    def _rule_medication_administration_documented(self, rule: MedicationAdministrationDocumented) -> list[Candidate]:
        admins = [m for m in self.meds if m.record_kind == "administration"]
        out = []
        for order in (m for m in self.meds if m.record_kind == "order"):
            k = drug_key(order.drug_name)
            match = [a for a in admins if drug_key(a.drug_name) == k and (
                order.ordered_at is None or a.administered_at is None or a.administered_at >= order.ordered_at)]
            if rule.window_minutes and order.ordered_at:
                match = [a for a in match if a.administered_at and
                         a.administered_at - order.ordered_at <= timedelta(minutes=rule.window_minutes)]
            if match:
                continue
            out.append(self._base(rule, rule.title,
                                  f"An order for {order.drug_name} is documented, but no matching administration entry was "
                                  f"identified{' within ' + _fmt_min(rule.window_minutes) if rule.window_minutes else ''}. "
                                  f"This is a documentation observation, not a medication error.",
                                  [_ev(order, "ordered_at")], occurred_at=order.ordered_at,
                                  uncertainty=["Administration may be recorded on a chart page not captured by extraction"],
                                  key=f"{k}:{order.ordered_at.isoformat() if order.ordered_at else order.id}"))
        return out

    def _rule_required_documentation(self, rule: RequiredDocumentation) -> list[Candidate]:
        missing = [t for t in rule.page_types if t not in self.page_types]
        if not missing:
            return []
        return [self._base(rule, rule.title, f"No page classified as {', '.join(missing)} was identified in the uploaded record.",
                           [], uncertainty=["Page classification may be wrong; check unclassified pages"],
                           key=",".join(missing))]

    # --- built-in documentation integrity checks -------------------------------------
    BUILTIN = {
        "missing_timestamp": ("low", "Note entries without a documented time",
                              "{n} note entr{y} {have} no documented time.", "documentation"),
        "missing_signature_marker": ("informational", "Note entries without a recognisable signature marker",
                                     "{n} note entr{y} {have} no signature or name marker in the extracted text.", "documentation"),
        "duplicate_page": ("low", "Duplicate pages in the record", "{n} page{s} {are} exact duplicate{s} of another page.", "documentation"),
        "possible_missing_pages": ("moderate", "Possible missing pages",
                                   "The record's own page numbering indicates pages that were not found in the upload.", "documentation"),
        "chronology_inconsistency": ("low", "Chronology inconsistency within a page",
                                     "{n} entr{y} {have} a time earlier than the preceding entry on the same page.", "documentation"),
        "possible_handwriting_or_illegible": ("informational", "Pages requiring manual transcription",
                                              "{n} page{s} {have} low-confidence text consistent with handwriting or poor scan quality.", None),
        "low_ocr_confidence": ("informational", "Low OCR confidence pages", "{n} page{s} {have} low OCR confidence.", None),
        "no_text_extracted": ("informational", "Pages with no extractable text",
                              "{n} page{s} produced no text (OCR unavailable or illegible). Manual review required.", None),
        "page_processing_failed": ("informational", "Pages that failed processing", "{n} page{s} could not be processed.", None),
    }
    BUILTIN_RECS = {
        "documentation": "Reinforce documentation standards for timed, signed entries and complete, paginated records.",
    }
    UNCERTAINTY = {
        "missing_signature_marker": "Signatures are often handwritten and may not appear in extracted text; verify on the page image",
        "missing_timestamp": "The time may be present in handwriting that was not captured",
        "possible_missing_pages": "Page numbering may be inconsistent in the source rather than pages being absent",
    }

    def builtin_checks(self) -> list[Candidate]:
        issues = self.db.scalars(select(ExtractedEntity).where(ExtractedEntity.case_id == self.case.id,
                                                               ExtractedEntity.entity_type == "documentation_issue")).all()
        by_kind: dict[str, list[ExtractedEntity]] = {}
        for i in issues:
            by_kind.setdefault(i.field_name, []).append(i)
        out = []
        for kind, rows in by_kind.items():
            if kind not in self.BUILTIN:
                continue
            sev, title, tmpl, rec_cat = self.BUILTIN[kind]
            n = len(rows)
            plural = n != 1
            desc = tmpl.format(n=n, y="ies" if plural else "y", s="s" if plural else "", have="have" if plural else "has",
                               are="are" if plural else "is")
            if kind == "possible_missing_pages":
                desc += " Missing page numbers: " + "; ".join(r.value_text or "" for r in rows) + "."
            category = "documentation_quality"
            rec = None
            if rec_cat and sev != "informational":
                rec = {"category": rec_cat, "description": self.BUILTIN_RECS[rec_cat], "priority": "low" if sev == "low" else "moderate",
                       "suggested_owner": "Medical Records / Quality Department"}
            out.append(Candidate(
                rule_id=f"{BUILTIN_RULESET}:{kind}", category=category, title=title, description=desc, severity=sev,
                evidence=[Evidence(r.document_id, r.page_number, r.excerpt, None, (r.normalized_value or {}).get("event_id"))
                          for r in rows[:25]],
                rule_source="builtin_documentation_check", protocol_id=BUILTIN_RULESET, protocol_version=BUILTIN_VERSION,
                uncertainty=[self.UNCERTAINTY[kind]] if kind in self.UNCERTAINTY else [],
                measurements={"count": n}, recommendation=rec, key=kind))
        return out


def evaluate_case(db: Session, case: PatientCase, *, rule_sets: list[ActiveRuleSet] | None = None,
                  include_builtin: bool = True) -> tuple[list[Candidate], dict]:
    warnings: list[str] = []
    if rule_sets is None:
        rule_sets, warnings = active_rule_sets(db)
    ev = RuleEvaluator(db, case)
    cands: list[Candidate] = []
    for rs in rule_sets:
        cands.extend(ev.evaluate(rs))
    if include_builtin:
        cands.extend(ev.builtin_checks())
    meta = {"protocols_applied": [{"protocol_id": rs.protocol.id, "name": rs.protocol.name, "version": rs.version.version,
                                   "version_id": rs.version.id, "rules": len(rs.rules.rules)} for rs in rule_sets],
            "builtin_ruleset": {"id": BUILTIN_RULESET, "version": BUILTIN_VERSION} if include_builtin else None,
            "warnings": warnings}
    if not rule_sets:
        meta["warnings"].append("No approved hospital protocol is configured: clinical threshold rules were not evaluated")
    return cands, meta


def persist_findings(db: Session, case: PatientCase, cands: list[Candidate], job_id: str | None) -> dict:
    """Upsert findings by fingerprint. Human review state on existing findings is preserved."""
    existing = {f.fingerprint: f for f in db.scalars(select(AuditFinding).where(AuditFinding.case_id == case.id))}
    keep: set[str] = set()
    created = updated = 0
    for c in cands:
        fp = c.fingerprint()
        keep.add(fp)
        f = existing.get(fp)
        if f is None:
            f = AuditFinding(case_id=case.id, fingerprint=fp, review_status="pending")
            db.add(f)
            created += 1
        else:
            updated += 1
            f.superseded = False
        f.category, f.title, f.description, f.severity = c.category, c.title, c.description, c.severity
        f.rule_id, f.rule_source, f.protocol_id, f.protocol_version = c.rule_id, c.rule_source, c.protocol_id, c.protocol_version
        f.occurred_at, f.uncertainty, f.job_id = c.occurred_at, c.uncertainty, job_id
        f.measurements = {**c.measurements, "recommendation_template": c.recommendation}
        f.confidence, f.confidence_method = None, "unavailable"  # deterministic rule: no probabilistic confidence
        f.requires_human_verification = True
        db.flush()
        db.query(EvidenceReference).filter(EvidenceReference.finding_id == f.id).delete()
        for e in c.evidence:
            db.add(EvidenceReference(case_id=case.id, finding_id=f.id, document_id=e.document_id, page_number=e.page_number,
                                     excerpt=e.excerpt, event_at=e.event_at, event_id=e.event_id))
    superseded = 0
    for fp, f in existing.items():
        if fp not in keep and not f.superseded:
            f.superseded = True
            superseded += 1
    db.flush()
    return {"created": created, "updated": updated, "superseded": superseded}
