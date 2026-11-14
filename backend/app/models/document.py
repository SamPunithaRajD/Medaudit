from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin
from app.models.types import EncryptedText, JSONType, UTCDateTime
from app.utils.ids import new_id, utcnow


class Document(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("DOC"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    storage_key: Mapped[str] = mapped_column(String(255))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    key_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(JSONType, default=dict)

    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                       order_by="DocumentPage.page_number")


class DocumentPage(Base, TimestampMixin):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="doc_page"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PG"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    source_member: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page_index: Mapped[int] = mapped_column(Integer, default=0)
    source_kind: Mapped[str] = mapped_column(String(10), default="pdf")  # pdf | image
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    duplicate_of_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_type: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    classification_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    classification_scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_review_reasons: Mapped[list] = mapped_column(JSONType, default=list)
    preprocessing: Mapped[dict] = mapped_column(JSONType, default=dict)

    document: Mapped[Document] = relationship(back_populates="pages")
    ocr_results: Mapped[list["OCRResult"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class OCRResult(Base):
    __tablename__ = "ocr_results"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("OCR"))
    page_id: Mapped[str] = mapped_column(ForeignKey("document_pages.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    # Page text and word boxes are encrypted at rest.
    text: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    words_json: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    mean_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_method: Mapped[str] = mapped_column(String(20), default="unavailable")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    low_confidence_word_count: Mapped[int] = mapped_column(Integer, default=0)
    handwriting_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    page: Mapped[DocumentPage] = relationship(back_populates="ocr_results")
