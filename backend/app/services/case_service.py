"""Upload handling: streaming size limits, type validation, archive inspection, dedupe, encrypted storage."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import Conflict, PayloadTooLarge, ValidationFailed
from app.document_intelligence.ingestion.sources import enumerate_pages
from app.document_intelligence.ingestion.validators import MEDIA_TYPES, safe_filename, validate_upload_type
from app.models import CaseAssignment, Document, PatientCase, ProcessingJob, User
from app.security.access_control import can_access_case
from app.services import storage
from app.services.processing_service import init_stages
from app.workers.queue import queue

CHUNK = 1024 * 1024


def read_limited(stream: BinaryIO, limit: int) -> bytes:
    """Read an upload in chunks, aborting as soon as it exceeds the limit (no unbounded buffering)."""
    with tempfile.SpooledTemporaryFile(max_size=16 * CHUNK) as tmp:
        total = 0
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise PayloadTooLarge(f"File exceeds the {limit // (1024 * 1024)} MiB limit", code="FILE_TOO_LARGE")
            tmp.write(chunk)
        if total == 0:
            raise ValidationFailed("Uploaded file is empty", details=[{"field": "file", "issue": "Empty file"}])
        tmp.seek(0)
        return tmp.read()


def parse_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("metadata must be a JSON object", details=[{"field": "metadata", "issue": "Invalid JSON"}]) from exc
    if not isinstance(data, dict) or len(raw) > 10_000:
        raise ValidationFailed("metadata must be a JSON object (max 10 KB)", details=[{"field": "metadata", "issue": "Invalid"}])
    return data


def create_case_from_upload(db: Session, user: User, *, filename: str | None, stream: BinaryIO,
                            department: str | None, patient_ref: str | None, metadata: dict,
                            idempotency_key: str | None = None, patient=None) -> tuple[PatientCase, Document, ProcessingJob]:
    s = get_settings()
    if idempotency_key:
        prior = db.scalar(select(Document).where(Document.uploaded_by == user.id, Document.deleted_at.is_(None))
                          .where(Document.metadata_json["idempotency_key"].as_string() == idempotency_key[:100]))
        if prior is not None:
            job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == prior.id)
                            .order_by(ProcessingJob.created_at.desc()))
            return db.get(PatientCase, prior.case_id), prior, job
    data = read_limited(stream, s.max_upload_bytes)
    kind = validate_upload_type(filename, data[:16])
    # Validate structure synchronously (cheap): archive safety + page limits, before storing anything.
    refs = enumerate_pages(data, kind)
    sha = hashlib.sha256(data).hexdigest()
    for dup in db.scalars(select(Document).where(Document.sha256 == sha, Document.deleted_at.is_(None))):
        if can_access_case(db, user, dup.case_id):
            raise Conflict("This file has already been uploaded", code="DUPLICATE_UPLOAD",
                           details=[{"field": "file", "issue": f"Duplicate of document {dup.id} in case {dup.case_id}"}])

    case = PatientCase(patient_ref=(patient_ref or "").strip()[:120] or None, department=(department or "").strip()[:120] or None,
                       status="processing", review_status="pending", processing_state="uploaded", created_by=user.id,
                       patient_id=patient.id if patient is not None else None,
                       metadata_json={k: v for k, v in metadata.items() if k not in ("patient_name", "patient_identifier")},
                       field_provenance={k: {"source": "uploader"} for k, v in (("department", department), ("patient_ref", patient_ref)) if v})
    db.add(case)
    db.flush()
    db.add(CaseAssignment(case_id=case.id, user_id=user.id, assignment_role="uploader"))
    doc = Document(case_id=case.id, filename=safe_filename(filename), media_type=MEDIA_TYPES[kind], size_bytes=len(data),
                   sha256=sha, page_count=len(refs), status="uploaded", storage_key="pending", uploaded_by=user.id,
                   metadata_json={"kind": kind, "original_filename": safe_filename(filename),
                                  "zip_members": sorted({r.source_member for r in refs if r.source_member})[:300],
                                  "idempotency_key": (idempotency_key or "")[:100] or None})
    db.add(doc)
    db.flush()
    doc.storage_key = storage.document_key(doc.id)
    doc.key_version = storage.put(doc.storage_key, data, aad=doc.id)
    job = ProcessingJob(case_id=case.id, document_id=doc.id, created_by=user.id, max_attempts=s.job_max_attempts,
                        total_pages=len(refs))
    db.add(job)
    db.flush()
    init_stages(db, job)
    queue.enqueue(db, job)
    case.processing_state = "queued"
    db.commit()
    return case, doc, job


def requeue_case(db: Session, user: User, case: PatientCase) -> ProcessingJob:
    """Start a new processing job for an existing case's latest document (reprocessing / manual retry)."""
    active = db.scalar(select(ProcessingJob).where(ProcessingJob.case_id == case.id,
                                                   ProcessingJob.status.not_in(["completed", "failed", "cancelled"])))
    if active is not None:
        raise Conflict("A processing job is already active for this case", code="JOB_ACTIVE")
    doc = db.scalar(select(Document).where(Document.case_id == case.id, Document.deleted_at.is_(None))
                    .order_by(Document.uploaded_at.desc()))
    if doc is None:
        raise ValidationFailed("Case has no document to process")
    job = ProcessingJob(case_id=case.id, document_id=doc.id, created_by=user.id,
                        max_attempts=get_settings().job_max_attempts, total_pages=doc.page_count or 0)
    db.add(job)
    db.flush()
    init_stages(db, job)
    queue.enqueue(db, job)
    case.processing_state = "queued"
    db.commit()
    return job


def delete_document_files(doc: Document) -> None:
    if doc.storage_key and doc.storage_key != "pending":
        storage.delete(doc.storage_key)
    folder = os.path.dirname(os.path.join(get_settings().storage_dir, doc.storage_key or ""))
    if os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
