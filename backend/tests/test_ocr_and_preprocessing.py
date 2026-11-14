import cv2
import numpy as np
import pytest

from app.document_intelligence.classification.classifier import classify, classify_rules
from app.document_intelligence.ingestion.sources import enumerate_pages, render_page
from app.document_intelligence.ocr.providers import PdfTextLayerProvider, TesseractProvider, run_ocr
from app.document_intelligence.preprocessing.image import estimate_skew, preprocess, rotate
from app.utils.sample_casesheet import build_sample_pdf, build_sample_zip_of_images


def text_image(angle=0.0):
    img = np.full((1400, 1100), 255, np.uint8)
    for i in range(12):
        cv2.putText(img, "VITAL SIGNS HR 112 BP 88/54 SpO2 95", (60, 120 + i * 90), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    return rotate(img, angle) if angle else img


def test_preprocess_pipeline_steps():
    res = preprocess(cv2.cvtColor(text_image(), cv2.COLOR_GRAY2RGB))
    assert res.steps[0] == "grayscale" and "adaptive_threshold" in res.steps
    assert set(np.unique(res.image)) <= {0, 255}


def test_deskew_estimates_rotation():
    assert abs(estimate_skew(text_image(4.0))) == pytest.approx(4.0, abs=1.0)


def test_low_resolution_flagged():
    res = preprocess(np.full((300, 400, 3), 255, np.uint8))
    assert not res.resolution_ok and any(w.startswith("low_resolution") for w in res.warnings)


def test_pdf_text_provider():
    data = build_sample_pdf()
    page = render_page(data, "pdf", enumerate_pages(data, "pdf")[2], with_image=False)
    out = PdfTextLayerProvider().recognize(page, None)
    assert "TPR CHART" in out.text and out.mean_confidence is None and out.confidence_method == "unavailable"


def test_image_without_ocr_engine_needs_manual_review(monkeypatch):
    monkeypatch.setattr(TesseractProvider, "available", lambda self: False)
    data = build_sample_zip_of_images(pages=1)
    page = render_page(data, "zip", enumerate_pages(data, "zip")[0])
    out, _ = run_ocr(page)
    assert out.provider == "none" and out.needs_manual_review


@pytest.mark.skipif(not TesseractProvider().available(), reason="Tesseract not installed")
def test_tesseract_on_scanned_image():
    data = build_sample_zip_of_images(pages=1, dpi=200)
    page = render_page(data, "zip", enumerate_pages(data, "zip")[0])
    out, pre = run_ocr(page)
    assert out.provider == "tesseract" and "EMERGENCY" in out.text.upper() and out.mean_confidence is not None


def test_classification_rules_and_llm_fallback():
    assert classify_rules("LABORATORY REPORT\nReference range 1-2\nSample collected").page_type == "laboratory"
    ambiguous = "Patient seen. Plan discussed."
    assert classify(ambiguous, lambda t, c: "doctor_notes").method == "llm"
    assert classify(ambiguous, lambda t, c: "not-a-category").method != "llm"
    assert classify("").page_type == "unknown"
