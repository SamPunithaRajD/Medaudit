from __future__ import annotations

import importlib
import os
import shutil
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger
from app.document_intelligence.ingestion.sources import RenderedPage
from app.document_intelligence.ocr.base import OCROutput, OCRProvider, OCRWord
from app.document_intelligence.preprocessing.image import PreprocessResult, preprocess

log = get_logger(__name__)


class PdfTextLayerProvider(OCRProvider):
    """Uses the embedded text layer of digital/searchable PDFs. Not OCR: no recognition confidence exists."""
    name = "pdf_text"
    needs_preprocessing = False

    def available(self) -> bool:
        return True

    def supports(self, page: RenderedPage) -> bool:
        return page.ref.source_kind == "pdf" and len(page.text_layer.strip()) >= 20

    def recognize(self, page: RenderedPage, pre: PreprocessResult | None) -> OCROutput:
        words = [OCRWord(w["text"], w["x"], w["y"], w["width"], w["height"], None) for w in page.text_words]
        return OCROutput(provider=self.name, text=page.text_layer, words=words, mean_confidence=None,
                         confidence_method="unavailable")


class TesseractProvider(OCRProvider):
    """Tesseract OCR. Reliable for printed text; NOT reliable for handwriting — low-confidence pages are flagged."""
    name = "tesseract"

    def __init__(self) -> None:
        import pytesseract
        self._pt = pytesseract
        cmd = get_settings().tesseract_cmd
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        else:
            default_win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if not shutil.which("tesseract") and os.path.exists(default_win):
                pytesseract.pytesseract.tesseract_cmd = default_win

    @lru_cache(maxsize=1)
    def available(self) -> bool:  # type: ignore[override]
        try:
            self._pt.get_tesseract_version()
            return True
        except Exception:
            return False

    def detect_rotation(self, page: RenderedPage) -> int | None:
        try:
            osd = self._pt.image_to_osd(page.image, output_type=self._pt.Output.DICT, timeout=30)
            if float(osd.get("orientation_conf", 0)) >= 2.0:
                return int(osd.get("rotate", 0))
        except Exception:
            return None
        return None

    def recognize(self, page: RenderedPage, pre: PreprocessResult | None) -> OCROutput:
        s = get_settings()
        img = pre.image if pre is not None else page.image
        data = self._pt.image_to_data(img, lang=s.tesseract_lang, config="--oem 1 --psm 3",
                                      output_type=self._pt.Output.DICT, timeout=s.ocr_page_timeout_seconds)
        h, w = img.shape[:2]
        words: list[OCRWord] = []
        lines: dict[tuple, list[str]] = {}
        for i, txt in enumerate(data["text"]):
            txt = (txt or "").strip()
            conf = float(data["conf"][i]) if str(data["conf"][i]) not in ("-1", "") else -1.0
            if not txt or conf < 0:
                continue
            words.append(OCRWord(txt, round(data["left"][i] / w, 4), round(data["top"][i] / h, 4),
                                 round(data["width"][i] / w, 4), round(data["height"][i] / h, 4), conf))
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(txt)
        text = "\n".join(" ".join(v) for _, v in sorted(lines.items()))
        confs = [x.conf for x in words if x.conf is not None]
        mean = round(sum(confs) / len(confs), 1) if confs else None
        low = sum(1 for c in confs if c < s.ocr_low_confidence_threshold)
        warnings: list[str] = []
        handwriting = False
        if mean is not None and mean < s.ocr_low_confidence_threshold:
            warnings.append(f"low_confidence_page:{mean}")
        if confs and low / len(confs) > 0.35:
            # Many low-confidence tokens is the typical signature of handwriting or degraded scans.
            handwriting = True
            warnings.append("possible_handwriting_or_illegible")
        return OCROutput(provider=self.name, text=text, words=words, mean_confidence=mean,
                         confidence_method="heuristic" if mean is not None else "unavailable",
                         low_confidence_words=low, handwriting_suspected=handwriting, warnings=warnings)


def _load_extra(path: str) -> OCRProvider | None:
    try:
        module, _, cls = path.partition(":")
        provider = getattr(importlib.import_module(module), cls)()
        if not isinstance(provider, OCRProvider):
            raise TypeError("Extra provider must subclass OCRProvider")
        return provider
    except Exception as exc:
        log.warning("Could not load OCR_EXTRA_PROVIDER: %s", type(exc).__name__)
        return None


_REGISTRY = {"pdf_text": PdfTextLayerProvider, "tesseract": TesseractProvider}


@lru_cache(maxsize=1)
def get_providers() -> tuple[OCRProvider, ...]:
    s = get_settings()
    providers: list[OCRProvider] = []
    if s.ocr_extra_provider:
        extra = _load_extra(s.ocr_extra_provider)
        if extra:
            providers.append(extra)
    for name in [n.strip() for n in s.ocr_providers.split(",") if n.strip()]:
        cls = _REGISTRY.get(name)
        if cls:
            providers.append(cls())
    return tuple(providers)


def provider_status() -> list[dict]:
    return [{"name": p.name, "available": bool(p.available())} for p in get_providers()]


def needs_raster(page_has_text_layer: bool) -> bool:
    """Whether a page must be rasterised (i.e. no earlier provider can use the text layer)."""
    for p in get_providers():
        if p.name == "pdf_text" and page_has_text_layer:
            return False
        if p.needs_preprocessing and p.available():
            return True
    return False


def run_ocr(page: RenderedPage) -> tuple[OCROutput, dict]:
    """Try providers in order; returns (output, preprocessing summary)."""
    pre_summary: dict = {}
    for provider in get_providers():
        if not provider.available() or not provider.supports(page):
            continue
        pre = None
        if provider.needs_preprocessing and page.image is not None:
            rotation = provider.detect_rotation(page)
            pre = preprocess(page.image, osd_rotation=rotation)
            pre_summary = pre.summary()
        out = provider.recognize(page, pre)
        if pre is not None:
            out.warnings.extend(pre.warnings)
        if out.text.strip() or provider is get_providers()[-1]:
            return out, pre_summary
    return OCROutput(provider="none", text="", warnings=["no_ocr_provider_available"]), pre_summary
