"""Pluggable OCR provider interface.

Providers are tried in the configured order (OCR_PROVIDERS). A handwriting-capable recogniser
can be added without code changes elsewhere by implementing `OCRProvider` and setting
OCR_EXTRA_PROVIDER="package.module:ClassName"; it is tried first.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.document_intelligence.ingestion.sources import RenderedPage
from app.document_intelligence.preprocessing.image import PreprocessResult


@dataclass
class OCRWord:
    text: str
    x: float
    y: float
    width: float
    height: float
    conf: float | None  # provider-specific 0–100; None when the provider has no notion of confidence


@dataclass
class OCROutput:
    provider: str
    text: str
    words: list[OCRWord] = field(default_factory=list)
    mean_confidence: float | None = None
    confidence_method: str = "unavailable"  # heuristic for engine-reported scores; never "calibrated" here
    low_confidence_words: int = 0
    handwriting_suspected: bool = False
    rotation: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_manual_review(self) -> bool:
        return self.handwriting_suspected or not self.text.strip() or any(
            w.startswith(("low_confidence", "no_ocr_provider")) for w in self.warnings)


class OCRProvider(ABC):
    name: str = "base"
    needs_preprocessing: bool = True

    @abstractmethod
    def available(self) -> bool: ...

    def supports(self, page: RenderedPage) -> bool:
        return page.image is not None

    @abstractmethod
    def recognize(self, page: RenderedPage, pre: PreprocessResult | None) -> OCROutput: ...

    def detect_rotation(self, page: RenderedPage) -> int | None:
        return None
