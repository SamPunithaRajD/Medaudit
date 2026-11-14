"""Agent framework. Agents read validated inputs (DB rows / page text), write their own outputs
idempotently, and report a summary. The LLM is optional for every agent: deterministic logic always
runs, and LLM additions are accepted only after schema + evidence validation."""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_engine.ollama.client import LLMResponseInvalid, LLMUnavailable, OllamaClient
from app.ai_engine.prompts.templates import clinical_items_prompt
from app.ai_engine.validators.llm_output import validate_items
from app.document_intelligence.extraction.extractor import PageText
from app.models import DocumentPage, OCRResult, PatientCase, ProcessingJob


@dataclass
class AgentContext:
    db: Session
    case: PatientCase
    job: ProcessingJob
    pages: list[PageText]
    llm: OllamaClient | None = None
    shared: dict[str, Any] = field(default_factory=dict)

    def pages_of(self, *types: str) -> list[PageText]:
        return [p for p in self.pages if p.page_type in types] if types else list(self.pages)

    def page_text(self, document_id: str | None, page_number: int | None) -> str | None:
        for p in self.pages:
            if p.document_id == document_id and p.page_number == page_number:
                return p.text
        return None

    def input_hash(self, *types: str) -> str:
        h = hashlib.sha256()
        for p in self.pages_of(*types):
            h.update(f"{p.document_id}:{p.page_number}:{p.page_type}:".encode())
            h.update(hashlib.sha256(p.text.encode()).digest())
        return h.hexdigest()


@dataclass
class AgentOutput:
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    llm_used: bool = False


class Agent(ABC):
    name: str = "agent"
    description: str = ""
    page_types: tuple[str, ...] = ()
    uses_llm: bool = False

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentOutput: ...

    def input_hash(self, ctx: AgentContext) -> str:
        return ctx.input_hash(*self.page_types)

    # --- LLM helper (validated) ----------------------------------------------
    def llm_items(self, ctx: AgentContext, page: PageText, purpose: str, fields: dict[str, str],
                  out: AgentOutput) -> list[dict]:
        if ctx.llm is None or not page.text.strip():
            return []
        system, prompt, schema = clinical_items_prompt(page.text, purpose, fields)
        try:
            res = ctx.llm.generate_json(system, prompt, schema)
        except (LLMUnavailable, LLMResponseInvalid) as exc:
            out.warnings.append(f"llm_unavailable:{type(exc).__name__}")
            return []
        out.llm_used = True
        items, rejected = validate_items(res.data, list(fields) + ["evidence"], page.text)
        if rejected:
            out.warnings.append(f"llm_items_rejected:{len(rejected)}")
        return items


def load_page_texts(db: Session, case_id: str) -> list[PageText]:
    rows = db.execute(
        select(DocumentPage, OCRResult)
        .join(OCRResult, (OCRResult.page_id == DocumentPage.id) & (OCRResult.is_primary.is_(True)), isouter=True)
        .where(DocumentPage.case_id == case_id)
        .order_by(DocumentPage.document_id, DocumentPage.page_number)).all()
    pages = []
    for page, ocr in rows:
        if page.duplicate_of_page is not None:
            continue  # exact duplicates are reported, not re-extracted
        pages.append(PageText(document_id=page.document_id, page_number=page.page_number, page_type=page.page_type,
                              text=(ocr.text if ocr and ocr.text else ""),
                              ocr_confidence=ocr.mean_confidence if ocr else None,
                              confidence_method=ocr.confidence_method if ocr else "unavailable"))
    return pages


def summarize(obj: Any) -> str:
    return json.dumps(obj, default=str)[:2000]
