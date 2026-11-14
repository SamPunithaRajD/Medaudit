"""End-to-end document processing pipeline executed by the background worker.

Job status (contract JobStatus) and persisted stages are updated truthfully as work happens.
Pages are processed one at a time in configurable batches; each page has its own retry budget
and failure isolation. A retried job resumes: completed pages and unchanged agents are skipped.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_engine.agents.base import AgentContext, load_page_texts
from app.ai_engine.ollama.client import LLMResponseInvalid, LLMUnavailable, get_ollama
from app.ai_engine.orchestration.orchestrator import CancelledError, Orchestrator
from app.ai_engine.prompts.templates import classification_prompt
from app.core.config import get_settings
from app.core.logging import get_logger
from app.document_intelligence.classification.classifier import classify
from app.document_intelligence.ingestion.sources import PageRef, enumerate_pages, render_page
from app.document_intelligence.ingestion.validators import sniff
from app.document_intelligence.ocr.providers import needs_raster, run_ocr
from app.models import Document, DocumentPage, JobStage, OCRResult, PatientCase, ProcessingJob
from app.services import storage
from app.utils.ids import utcnow

log = get_logger(__name__)

STAGE_DEFS = [("ingest", "Ingest document"), ("preprocess", "Image preprocessing"), ("ocr", "Text extraction / OCR"),
              ("classify", "Page classification"), ("extract_entities", "Clinical entity extraction"),
              ("analyze", "Specialised agent analysis"), ("timeline", "Timeline & rule evaluation"),
              ("verify_evidence", "Evidence verification"), ("report", "Report generation")]
WEIGHTS = {"ingest": 5, "ocr": 55, "classify": 5, "analyze": 20, "timeline": 5, "verify_evidence": 5, "report": 5}


class JobFailed(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def init_stages(db: Session, job: ProcessingJob) -> None:
    existing = {s.key for s in job.stages}
    for order, (key, label) in enumerate(STAGE_DEFS):
        if key not in existing:
            db.add(JobStage(job_id=job.id, key=key, label=label, order=order, status="queued"))
    db.flush()


class Pipeline:
    def __init__(self, db: Session, job: ProcessingJob, worker_id: str = "inline"):
        self.db = db
        self.job = job
        self.worker_id = worker_id
        self.s = get_settings()
        self.case = db.get(PatientCase, job.case_id)
        self.document = db.get(Document, job.document_id) if job.document_id else None
        self._llm = None
        self._llm_checked = False

    # --- helpers ------------------------------------------------------------------
    @property
    def llm(self):
        if not self._llm_checked:
            self._llm_checked = True
            client = get_ollama()
            try:
                self._llm = client if client.available() else None
            except Exception:
                self._llm = None
            if self._llm is None:
                self._warn("Local LLM unavailable: deterministic extraction only")
        return self._llm

    def _warn(self, msg: str) -> None:
        if msg not in (self.job.warnings or []):
            self.job.warnings = [*(self.job.warnings or []), msg][:50]

    def _stage(self, key: str, status: str, **detail) -> None:
        st = next((s for s in self.job.stages if s.key == key), None)
        if st is None:
            return
        if status == "running" and st.status != "running":
            st.started_at = st.started_at or utcnow()
        if status in ("completed", "failed"):
            st.completed_at = utcnow()
        st.status = status
        if detail:
            st.detail = {**(st.detail or {}), **detail}
        self._progress()
        self.db.commit()

    def _status(self, status: str, stage: str | None = None, processing_state: str | None = None) -> None:
        self.job.status = status
        self.job.current_stage = stage
        self.job.heartbeat_at = utcnow()
        if processing_state:
            self.case.processing_state = processing_state
        self._progress()
        self.db.commit()

    def _progress(self) -> None:
        done = 0.0
        for s in self.job.stages:
            w = WEIGHTS.get(s.key, 0)
            if s.status == "completed":
                done += w
            elif s.key == "ocr" and s.status == "running" and self.job.total_pages:
                done += w * (self.job.completed_pages + self.job.failed_pages) / self.job.total_pages
        self.job.progress_percent = round(min(done, 100.0), 1)

    def _check_cancel(self) -> None:
        self.db.refresh(self.job, attribute_names=["cancel_requested"])
        if self.job.cancel_requested:
            raise CancelledError()

    def _llm_classifier(self):
        if not self.s.llm_classification_enabled or self.llm is None:
            return None
        llm = self.llm

        def _classify(text: str, candidates: list[str]) -> str | None:
            system, prompt, schema = classification_prompt(text, candidates)
            try:
                return llm.generate_json(system, prompt, schema).data.get("page_type")
            except (LLMUnavailable, LLMResponseInvalid, AttributeError):
                return None
        return _classify

    # --- stages ----------------------------------------------------------------------
    def load_original(self) -> tuple[bytes, str]:
        doc = self.document
        try:
            data = storage.get(doc.storage_key, aad=doc.id)
        except FileNotFoundError as exc:
            raise JobFailed("DOCUMENT_MISSING", "Stored document not found") from exc
        except Exception as exc:
            raise JobFailed("DOCUMENT_INTEGRITY", "Stored document failed decryption/integrity check") from exc
        if hashlib.sha256(data).hexdigest() != doc.sha256:
            raise JobFailed("DOCUMENT_INTEGRITY", "Stored document checksum mismatch")
        kind = sniff(data[:16])
        if kind not in ("pdf", "zip"):
            raise JobFailed("UNSUPPORTED_MEDIA_TYPE", "Stored document has an unsupported type")
        return data, kind

    def ingest(self, data: bytes, kind: str) -> list[PageRef]:
        self._stage("ingest", "running")
        try:
            refs = enumerate_pages(data, kind)
        except Exception as exc:
            raise JobFailed(getattr(exc, "code", "INVALID_DOCUMENT"), getattr(exc, "message", str(exc))[:300]) from exc
        existing = {p.page_number for p in self.db.scalars(select(DocumentPage).where(DocumentPage.document_id == self.document.id))}
        for r in refs:
            if r.page_number not in existing:
                self.db.add(DocumentPage(document_id=self.document.id, case_id=self.case.id, page_number=r.page_number,
                                         source_member=r.source_member, source_page_index=r.source_page_index,
                                         source_kind=r.source_kind, status="pending"))
        self.document.page_count = len(refs)
        self.document.status = "processing"
        self.job.total_pages = len(refs)
        self._stage("ingest", "completed", pages=len(refs))
        return refs

    def ocr_pages(self, data: bytes, kind: str, refs: list[PageRef]) -> None:
        self._status("preprocessing", "preprocess")
        self._stage("preprocess", "running")
        self._stage("ocr", "running")
        self._status("extracting", "ocr")
        pages = {p.page_number: p for p in self.db.scalars(select(DocumentPage).where(DocumentPage.document_id == self.document.id))}
        hashes = {p.sha256: p.page_number for p in pages.values() if p.sha256 and p.duplicate_of_page is None}
        done = sum(1 for p in pages.values() if p.status in ("ocr_completed", "needs_manual_review"))
        failed = sum(1 for p in pages.values() if p.status == "failed" and p.attempts > self.s.page_max_retries)
        self.job.completed_pages, self.job.failed_pages = done, failed
        todo = [r for r in refs if pages[r.page_number].status not in ("ocr_completed", "needs_manual_review")
                and not (pages[r.page_number].status == "failed" and pages[r.page_number].attempts > self.s.page_max_retries)]
        providers_used: dict[str, int] = {}
        for i in range(0, len(todo), self.s.page_batch_size):
            self._check_cancel()
            for ref in todo[i:i + self.s.page_batch_size]:
                page = pages[ref.page_number]
                while True:
                    page.attempts += 1
                    page.status = "processing"
                    rendered = None
                    try:
                        if ref.source_kind == "pdf":
                            # Cheap probe first: digital PDFs with a text layer never need rasterising.
                            probe = render_page(data, kind, ref, with_image=False)
                            has_layer = len(probe.text_layer.strip()) >= 20
                            rendered = render_page(data, kind, ref, with_image=True) if needs_raster(has_layer) else probe
                        else:
                            has_layer = False
                            rendered = render_page(data, kind, ref, with_image=True)
                        page.width, page.height, page.has_text_layer = rendered.width, rendered.height, has_layer
                        page.sha256 = rendered.sha256
                        if rendered.sha256 in hashes and hashes[rendered.sha256] != page.page_number:
                            page.duplicate_of_page = hashes[rendered.sha256]
                        else:
                            hashes[rendered.sha256] = page.page_number
                        out, pre = run_ocr(rendered)
                        for old in list(page.ocr_results):
                            self.db.delete(old)
                        self.db.flush()
                        self.db.add(OCRResult(
                            page_id=page.id, provider=out.provider, is_primary=True, text=out.text,
                            words_json=json.dumps([w.__dict__ for w in out.words[:5000]]),
                            mean_confidence=out.mean_confidence, confidence_method=out.confidence_method,
                            word_count=len(out.words), low_confidence_word_count=out.low_confidence_words,
                            handwriting_suspected=out.handwriting_suspected, warnings=out.warnings))
                        page.preprocessing = pre
                        page.needs_manual_review = out.needs_manual_review
                        page.manual_review_reasons = [w for w in out.warnings] or (["no_text"] if not out.text.strip() else [])
                        page.status = "needs_manual_review" if out.needs_manual_review else "ocr_completed"
                        page.error = None
                        providers_used[out.provider] = providers_used.get(out.provider, 0) + 1
                        self.job.completed_pages += 1
                        break
                    except Exception as exc:
                        page.error = f"{type(exc).__name__}: {str(exc)[:200]}"
                        page.status = "failed"
                        log.warning("Page %s failed (attempt %s): %s", page.page_number, page.attempts, type(exc).__name__)
                        if page.attempts > self.s.page_max_retries:
                            self.job.failed_pages += 1
                            break
                    finally:
                        rendered = None  # release page image memory before the next page
            self.job.heartbeat_at = utcnow()
            self._progress()
            self.db.commit()
        if "none" in providers_used:
            self._warn("Some pages had no usable OCR provider (install Tesseract for scanned images)")
        if self.job.total_pages and self.job.failed_pages >= self.job.total_pages:
            raise JobFailed("ALL_PAGES_FAILED", "No page could be processed", retryable=True)
        self._stage("preprocess", "completed")
        self._stage("ocr", "completed", providers=providers_used, failed_pages=self.job.failed_pages)
        self._status("extracting", "ocr", processing_state="ocr_completed")

    def classify_pages(self) -> None:
        self._status("classifying", "classify")
        self._stage("classify", "running")
        llm_cls = self._llm_classifier()
        counts: dict[str, int] = {}
        rows = self.db.execute(select(DocumentPage, OCRResult).join(
            OCRResult, (OCRResult.page_id == DocumentPage.id) & (OCRResult.is_primary.is_(True)), isouter=True)
            .where(DocumentPage.document_id == self.document.id)).all()
        for page, ocr in rows:
            c = classify(ocr.text if ocr and ocr.text else "", llm_cls)
            page.page_type, page.classification_method, page.classification_scores = c.page_type, c.method, c.scores
            counts[c.page_type] = counts.get(c.page_type, 0) + 1
        self._stage("classify", "completed", page_types=counts)

    def analyze(self) -> None:
        ctx = AgentContext(db=self.db, case=self.case, job=self.job, pages=load_page_texts(self.db, self.case.id),
                           llm=self.llm)
        orch = Orchestrator(self.db, ctx, should_cancel=lambda: (self.db.refresh(self.job, attribute_names=["cancel_requested"])
                                                                 or self.job.cancel_requested))
        self._status("analyzing", "analyze")
        self._stage("extract_entities", "running")
        self._stage("analyze", "running")
        res = orch.run({"analyze"})
        self._stage("extract_entities", "completed")
        self._stage("analyze", "completed", agents=res)
        self._status("building_timeline", "timeline", processing_state="ai_analysis_completed")
        self._stage("timeline", "running")
        res.update(orch.run({"timeline"}))
        self._stage("timeline", "completed")
        self._status("verifying_evidence", "verify_evidence")
        self._stage("verify_evidence", "running")
        res.update(orch.run({"verify_evidence"}))
        self._stage("verify_evidence", "completed")
        self._status("generating_report", "report")
        self._stage("report", "running")
        res.update(orch.run({"report"}))
        self._stage("report", "completed")
        failed = [k for k, v in res.items() if v == "failed"]
        if failed:
            self._warn(f"Agents failed (partial results): {', '.join(failed)}")

    def run(self) -> None:
        job = self.job
        job.started_at = job.started_at or utcnow()
        init_stages(self.db, job)
        self.db.refresh(job)
        self._status("validating", "ingest", processing_state="processing")
        data, kind = self.load_original()
        self._status("ingesting", "ingest")
        refs = self.ingest(data, kind)
        self.ocr_pages(data, kind, refs)
        del data
        self.classify_pages()
        self.analyze()
        self.document.status = "ready"
        job.status, job.current_stage, job.completed_at = "completed", None, utcnow()
        job.progress_percent = 100.0
        job.error_code = job.error_message = None
        self.case.processing_state = "verification_pending"
        if self.case.status in ("processing", "failed"):
            self.case.status = "pending_review"
        self.db.commit()


def execute_job(db: Session, job_id: str, worker_id: str = "inline") -> ProcessingJob:
    """Run a claimed job with failure handling and bounded retries."""
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise ValueError("job not found")
    pipe = Pipeline(db, job, worker_id)
    try:
        pipe.run()
    except CancelledError:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        job.status, job.completed_at, job.error_code = "cancelled", utcnow(), "CANCELLED"
        job.error_message = "Cancelled by user"
        _fail_running_stages(job)
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        code = exc.code if isinstance(exc, JobFailed) else "PROCESSING_ERROR"
        message = exc.message if isinstance(exc, JobFailed) else "Unexpected processing error"
        retryable = exc.retryable if isinstance(exc, JobFailed) else True
        log.error("Job %s failed: %s", job_id, code)
        job.error_code, job.error_message = code, message
        job.retryable = retryable and job.attempts < job.max_attempts
        _fail_running_stages(job)
        if job.retryable:
            job.status = "queued"  # picked up again by the worker (attempt counter bounds retries)
        else:
            job.status, job.completed_at = "failed", utcnow()
            case = db.get(PatientCase, job.case_id)
            case.processing_state = "failed"
            if case.status == "processing":
                case.status = "failed"
            if job.document_id:
                doc = db.get(Document, job.document_id)
                if doc:
                    doc.status = "failed"
    finally:
        job = db.get(ProcessingJob, job_id)
        if job is not None:
            job.locked_by = None
            job.locked_at = None
            db.commit()
    return job


def _fail_running_stages(job: ProcessingJob) -> None:
    for st in job.stages:
        if st.status == "running":
            st.status, st.completed_at = "failed", utcnow()
