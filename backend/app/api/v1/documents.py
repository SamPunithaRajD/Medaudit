from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_db, paginate, pagination, require
from app.core.exceptions import NotFound, ServiceUnavailable
from app.document_intelligence.ingestion.sources import PageRef, render_preview_png
from app.models import Document, DocumentPage, EvidenceReference, OCRResult
from app.security.access_control import CurrentUser, get_case_for_user
from app.security.audit_logging import record_event
from app.services import serializers as S
from app.services import storage
from app.utils.ids import iso

router = APIRouter(prefix="/api/v1", tags=["documents"])
NO_STORE = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"}


def _doc(db: Session, cu: CurrentUser, document_id: str, permission: str = "document.read") -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise NotFound("Document not found", code="DOCUMENT_NOT_FOUND")
    get_case_for_user(db, cu.user, doc.case_id, permission)
    return doc


def _page(db: Session, doc: Document, page_number: int) -> DocumentPage:
    page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == doc.id, DocumentPage.page_number == page_number))
    if page is None:
        raise NotFound("Page not found", code="PAGE_NOT_FOUND")
    return page


def _primary_ocr(db: Session, page: DocumentPage) -> OCRResult | None:
    return db.scalar(select(OCRResult).where(OCRResult.page_id == page.id, OCRResult.is_primary.is_(True)))


@router.get("/cases/{case_id}/documents")
def case_documents(case_id: str, p: Pagination = Depends(pagination), db: Session = Depends(get_db),
                   cu: CurrentUser = Depends(require("document.read"))):
    case = get_case_for_user(db, cu.user, case_id, "document.read")
    q = select(Document).where(Document.case_id == case.id, Document.deleted_at.is_(None)).order_by(Document.uploaded_at)
    return paginate(db, q, p, lambda rows: [S.document(d) for d in rows])


@router.get("/documents/{document_id}")
def document_detail(document_id: str, p: Pagination = Depends(pagination), db: Session = Depends(get_db),
                    cu: CurrentUser = Depends(require("document.read"))):
    doc = _doc(db, cu, document_id)
    q = select(DocumentPage).where(DocumentPage.document_id == doc.id).order_by(DocumentPage.page_number)
    pages = paginate(db, q, p, lambda rows: [_page_meta(db, doc, pg) for pg in rows])
    return {"document": {**S.document(doc), "metadata": {k: v for k, v in (doc.metadata_json or {}).items()
                                                        if k != "idempotency_key"}}, "pages": pages}


def _page_meta(db: Session, doc: Document, pg: DocumentPage) -> dict:
    ocr = _primary_ocr(db, pg)
    return {"document_id": doc.id, "page_number": pg.page_number, "status": pg.status, "page_type": pg.page_type,
            "classification_method": pg.classification_method, "classification_scores": pg.classification_scores,
            "source_member": pg.source_member, "source_page_index": pg.source_page_index, "width": pg.width,
            "height": pg.height, "has_text_layer": pg.has_text_layer, "duplicate_of_page": pg.duplicate_of_page,
            "needs_manual_review": pg.needs_manual_review, "manual_review_reasons": pg.manual_review_reasons,
            "error": pg.error, "attempts": pg.attempts, "preprocessing": pg.preprocessing,
            "ocr": {"provider": ocr.provider, "mean_confidence": ocr.mean_confidence,
                    "confidence_method": ocr.confidence_method, "word_count": ocr.word_count,
                    "low_confidence_word_count": ocr.low_confidence_word_count,
                    "handwriting_suspected": ocr.handwriting_suspected, "warnings": ocr.warnings} if ocr else None,
            "preview_url": f"/api/v1/documents/{doc.id}/pages/{pg.page_number}/preview",
            "text_url": f"/api/v1/documents/{doc.id}/pages/{pg.page_number}/text",
            "updated_at": iso(pg.updated_at)}


@router.get("/documents/{document_id}/pages/{page_number}")
def page_detail(document_id: str, page_number: int, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("document.read"))):
    doc = _doc(db, cu, document_id)
    pg = _page(db, doc, page_number)
    evidence = db.scalars(select(EvidenceReference).where(EvidenceReference.document_id == doc.id,
                                                          EvidenceReference.page_number == page_number,
                                                          EvidenceReference.finding_id.is_not(None))).all()
    return {**_page_meta(db, doc, pg), "finding_evidence": [S.evidence(e) for e in evidence]}


@router.get("/documents/{document_id}/pages/{page_number}/text")
def page_text(document_id: str, page_number: int, include_words: bool = Query(False), db: Session = Depends(get_db),
              cu: CurrentUser = Depends(require("document.read"))):
    doc = _doc(db, cu, document_id)
    pg = _page(db, doc, page_number)
    ocr = _primary_ocr(db, pg)
    record_event(db, "document.page_text_view", actor=cu.user, resource_type="document", resource_id=doc.id,
                 case_id=doc.case_id, ip_hash=cu.ip_hash, details={"page": page_number})
    body = {"document_id": doc.id, "page_number": page_number, "text": ocr.text if ocr else None,
            "provider": ocr.provider if ocr else None, "mean_confidence": ocr.mean_confidence if ocr else None,
            "confidence_method": ocr.confidence_method if ocr else "unavailable",
            "needs_manual_review": pg.needs_manual_review, "status": pg.status}
    if include_words and ocr and ocr.words_json:
        body["words"] = json.loads(ocr.words_json)
        body["coordinate_space"] = "normalized_0_1"
    return body


@router.get("/documents/{document_id}/pages/{page_number}/preview", response_class=Response,
            responses={200: {"content": {"image/png": {}}}})
def page_preview(document_id: str, page_number: int, db: Session = Depends(get_db),
                 cu: CurrentUser = Depends(require("document.read"))):
    doc = _doc(db, cu, document_id)
    pg = _page(db, doc, page_number)
    try:
        data = storage.get(doc.storage_key, aad=doc.id)
    except Exception as exc:
        raise ServiceUnavailable("Document could not be read", code="DOCUMENT_UNAVAILABLE") from exc
    kind = (doc.metadata_json or {}).get("kind", "pdf")
    png = render_preview_png(data, kind, PageRef(pg.page_number, pg.source_member, pg.source_page_index, pg.source_kind))
    record_event(db, "document.page_preview", actor=cu.user, resource_type="document", resource_id=doc.id,
                 case_id=doc.case_id, ip_hash=cu.ip_hash, details={"page": page_number})
    return Response(png, media_type="image/png", headers=NO_STORE)


@router.get("/documents/{document_id}/download", response_class=Response)
def download(document_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("document.download"))):
    doc = _doc(db, cu, document_id, "document.download")
    try:
        data = storage.get(doc.storage_key, aad=doc.id)
    except Exception as exc:
        record_event(db, "document.download", actor=cu.user, resource_type="document", resource_id=doc.id,
                     case_id=doc.case_id, outcome="failure", ip_hash=cu.ip_hash)
        raise ServiceUnavailable("Document failed integrity verification", code="DOCUMENT_INTEGRITY") from exc
    record_event(db, "document.download", actor=cu.user, resource_type="document", resource_id=doc.id,
                 case_id=doc.case_id, ip_hash=cu.ip_hash, details={"size_bytes": len(data)})
    return Response(data, media_type=doc.media_type,
                    headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{doc.filename}"'})


@router.get("/evidence/{evidence_id}")
def evidence_detail(evidence_id: str, db: Session = Depends(get_db), cu: CurrentUser = Depends(require("document.read"))):
    ev = db.get(EvidenceReference, evidence_id)
    if ev is None:
        raise NotFound("Evidence not found")
    get_case_for_user(db, cu.user, ev.case_id, "document.read")
    return {"evidence": S.evidence(ev),
            "page_url": f"/api/v1/documents/{ev.document_id}/pages/{ev.page_number}" if ev.document_id else None}
