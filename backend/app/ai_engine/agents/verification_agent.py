"""Agent 10 — Evidence verification. Checks every evidence reference against the source page text."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from app.ai_engine.agents.base import Agent, AgentContext, AgentOutput
from app.ai_engine.validators.llm_output import quote_supported
from app.models import AuditFinding, ClinicalEvent, DocumentPage, EvidenceReference


class EvidenceVerificationAgent(Agent):
    name = "evidence_verification"
    description = "Verifies that each finding/event is supported by source text on the cited page; flags unsupported claims."

    def run(self, ctx: AgentContext) -> AgentOutput:
        out = AgentOutput()
        stats: Counter = Counter()
        evidences = ctx.db.scalars(select(EvidenceReference).where(EvidenceReference.case_id == ctx.case.id)).all()
        by_finding: dict[str, list[EvidenceReference]] = {}
        by_event: dict[str, EvidenceReference] = {}
        # All stored pages, including exact duplicates that are excluded from extraction.
        existing_pages = set(ctx.db.execute(select(DocumentPage.document_id, DocumentPage.page_number)
                                            .where(DocumentPage.case_id == ctx.case.id)).all())
        for ev in evidences:
            text = ctx.page_text(ev.document_id, ev.page_number)
            if not ev.excerpt and ev.page_number and (ev.document_id, ev.page_number) in existing_pages:
                ev.verification_status, ev.verification_note = "verified", "Page-level reference: cited page exists"
            elif text is None:
                ev.verification_status, ev.verification_note = "disputed", "Cited page not found in processed pages"
            elif not ev.excerpt:
                ev.verification_status, ev.verification_note = "unverified", "No excerpt to verify"
            elif quote_supported(ev.excerpt, text):
                ev.verification_status, ev.verification_note = "verified", "Excerpt found on cited page"
            else:
                ev.verification_status, ev.verification_note = "disputed", "Excerpt not found on cited page"
            p = next((p for p in ctx.pages if p.document_id == ev.document_id and p.page_number == ev.page_number), None)
            if p is not None and p.ocr_confidence is not None:
                ev.extraction_confidence, ev.confidence_method = round(p.ocr_confidence / 100, 3), "heuristic"
            stats[ev.verification_status] += 1
            if ev.finding_id:
                by_finding.setdefault(ev.finding_id, []).append(ev)
            if ev.event_id and not ev.finding_id:
                by_event[ev.event_id] = ev

        for ev_row in ctx.db.scalars(select(ClinicalEvent).where(ClinicalEvent.case_id == ctx.case.id)):
            ref = by_event.get(ev_row.id)
            if ref is not None:
                ev_row.verification_status = ref.verification_status

        f_stats: Counter = Counter()
        for f in ctx.db.scalars(select(AuditFinding).where(AuditFinding.case_id == ctx.case.id,
                                                           AuditFinding.superseded.is_(False))):
            refs = by_finding.get(f.id, [])
            absence = ("required" in ((f.measurements or {}).get("rule", {}) or {}).get("type", "")
                       or (f.rule_id or "").endswith(":possible_missing_pages"))
            if absence:
                # Absence-type findings (missing pages / required page type not found) have no quotable excerpt;
                # they rest on the whole processed document set and are labelled as such.
                f.verification_status = "absence_based"
            elif not refs:
                f.verification_status = "unsupported"
            elif all(r.verification_status == "verified" for r in refs):
                f.verification_status = "verified"
            elif any(r.verification_status == "verified" for r in refs):
                f.verification_status = "partially_verified"
            else:
                f.verification_status = "unsupported"
            if f.verification_status == "unsupported":
                f.uncertainty = list({*f.uncertainty, "Evidence could not be verified against source text; excluded from preventability scoring"})
            f_stats[f.verification_status] += 1
        ctx.db.flush()
        out.summary = {"evidence": dict(stats), "findings": dict(f_stats),
                       "evidence_support_rate": round(stats["verified"] / max(sum(stats.values()), 1), 3)}
        return out
