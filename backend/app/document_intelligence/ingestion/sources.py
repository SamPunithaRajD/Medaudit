"""Enumerate and render pages from a stored PDF or ZIP one at a time (bounded memory)."""
from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.document_intelligence.ingestion.zip_safety import InvalidArchive, inspect_zip, read_member


class InvalidDocument(AppError):
    """Invalid document"""
    status_code, code = 422, "INVALID_DOCUMENT"


@dataclass
class PageRef:
    page_number: int  # 1-based, across the whole upload
    source_member: str | None
    source_page_index: int
    source_kind: str  # pdf | image


@dataclass
class RenderedPage:
    ref: PageRef
    image: np.ndarray | None  # RGB uint8
    width: int
    height: int
    text_layer: str = ""
    text_words: list[dict] = field(default_factory=list)  # normalised boxes
    sha256: str = ""


def _open_pdf(data: bytes) -> fitz.Document:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise InvalidDocument("PDF could not be parsed") from exc
    if doc.needs_pass:
        raise InvalidDocument("Password-protected PDFs are not supported")
    return doc


def enumerate_pages(data: bytes, kind: str) -> list[PageRef]:
    s = get_settings()
    refs: list[PageRef] = []
    if kind == "pdf":
        with _open_pdf(data) as doc:
            for i in range(doc.page_count):
                refs.append(PageRef(len(refs) + 1, None, i, "pdf"))
    elif kind == "zip":
        for m in inspect_zip(data):
            if m.kind == "pdf":
                with _open_pdf(read_member(data, m.name, s.max_upload_bytes * 4)) as doc:
                    for i in range(doc.page_count):
                        refs.append(PageRef(len(refs) + 1, m.name, i, "pdf"))
            else:
                refs.append(PageRef(len(refs) + 1, m.name, 0, "image"))
            if len(refs) > s.max_pdf_pages:
                break
    else:
        raise InvalidDocument("Unsupported document kind")
    if len(refs) > s.max_pdf_pages:
        raise InvalidDocument(f"Document exceeds the maximum of {s.max_pdf_pages} pages", code="TOO_MANY_PAGES")
    if not refs:
        raise InvalidDocument("Document contains no pages")
    return refs


def _pix_to_array(pix: fitz.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    elif pix.n == 1:
        arr = np.repeat(arr, 3, axis=2)
    return arr.copy()


def _render_pdf_page(doc: fitz.Document, index: int, dpi: int, with_image: bool) -> tuple[np.ndarray | None, int, int, str, list[dict]]:
    page = doc.load_page(index)
    rect = page.rect
    text = page.get_text("text") or ""
    words = []
    for x0, y0, x1, y1, w, *_ in page.get_text("words"):
        words.append({"text": w, "x": round(x0 / rect.width, 4), "y": round(y0 / rect.height, 4),
                      "width": round((x1 - x0) / rect.width, 4), "height": round((y1 - y0) / rect.height, 4),
                      "conf": None})
    img = None
    w_px, h_px = int(rect.width * dpi / 72), int(rect.height * dpi / 72)
    if with_image:
        if w_px * h_px > get_settings().max_image_pixels:
            dpi = max(72, int(dpi * (get_settings().max_image_pixels / (w_px * h_px)) ** 0.5))
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
        img = _pix_to_array(pix)
        h_px, w_px = img.shape[:2]
    return img, w_px, h_px, text, words


def _load_image(data: bytes) -> np.ndarray:
    Image.MAX_IMAGE_PIXELS = get_settings().max_image_pixels
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)
            return np.array(im.convert("RGB"))
    except Image.DecompressionBombError as exc:
        raise InvalidDocument("Image exceeds the permitted pixel count") from exc
    except Exception as exc:
        raise InvalidDocument("Image could not be decoded") from exc


def render_page(data: bytes, kind: str, ref: PageRef, *, dpi: int | None = None, with_image: bool = True,
                text_only_if_layer: bool = False) -> RenderedPage:
    """Render a single page. If `text_only_if_layer`, skip rasterising PDF pages that have a usable text layer."""
    s = get_settings()
    dpi = dpi or s.ocr_render_dpi
    if ref.source_kind == "pdf":
        pdf_bytes = data if kind == "pdf" else read_member(data, ref.source_member or "", s.max_upload_bytes * 4)
        with _open_pdf(pdf_bytes) as doc:
            img, w, h, text, words = _render_pdf_page(doc, ref.source_page_index, dpi, with_image=False)
            needs_img = with_image and not (text_only_if_layer and len(text.strip()) >= 20)
            if needs_img:
                img, w, h, text, words = _render_pdf_page(doc, ref.source_page_index, dpi, with_image=True)
        digest_src = img.tobytes() if img is not None else text.encode()
        return RenderedPage(ref, img, w, h, text, words, hashlib.sha256(digest_src).hexdigest())
    raw = read_member(data, ref.source_member or "", s.max_upload_bytes * 4)
    img = _load_image(raw)
    return RenderedPage(ref, img, img.shape[1], img.shape[0], "", [], hashlib.sha256(raw).hexdigest())


def render_preview_png(data: bytes, kind: str, ref: PageRef, max_width: int = 1200) -> bytes:
    page = render_page(data, kind, ref, dpi=110)
    im = Image.fromarray(page.image)
    if im.width > max_width:
        im = im.resize((max_width, int(im.height * max_width / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


__all__ = ["InvalidArchive", "InvalidDocument", "PageRef", "RenderedPage", "enumerate_pages", "render_page",
           "render_preview_png"]
