"""Validation of LLM outputs: schema checks, evidence-quote verification and unsafe-language screening."""
from __future__ import annotations

import difflib
import re
from typing import Any

_WS = re.compile(r"\s+")

# Language the system must never emit as its own conclusion.
UNSAFE_CONCLUSIONS = re.compile(
    r"\b(negligen\w*|malpractice|liable|liability|at\s+fault|was\s+preventable|could\s+have\s+been\s+prevented|"
    r"caused\s+(?:the\s+)?death|responsible\s+for\s+(?:the\s+)?death|should\s+(?:be\s+)?(?:prescribe|administer|give))\b",
    re.I)


def normalise(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip().lower()


def quote_supported(quote: str | None, source_text: str, threshold: float = 0.88) -> bool:
    """True if `quote` appears (near-)verbatim in source_text. Fuzzy only to tolerate whitespace/OCR noise."""
    q = normalise(quote or "")
    src = normalise(source_text)
    if len(q) < 4:
        return False
    if q in src:
        return True
    window = len(q)
    best = 0.0
    step = max(1, window // 4)
    for i in range(0, max(1, len(src) - window + 1), step):
        best = max(best, difflib.SequenceMatcher(None, q, src[i:i + window]).ratio())
        if best >= threshold:
            return True
    return False


def validate_items(data: Any, required: list[str], source_text: str) -> tuple[list[dict], list[str]]:
    """Keep only well-formed items whose evidence quote is found in the source. Returns (items, rejections)."""
    rejections: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return [], ["LLM output did not match the expected schema"]
    kept: list[dict] = []
    for item in data["items"][:200]:
        if not isinstance(item, dict) or any(k not in item for k in required):
            rejections.append("Malformed item")
            continue
        if not quote_supported(item.get("evidence"), source_text):
            rejections.append("Evidence quote not found in source text")
            continue
        kept.append({k: (str(v)[:500] if v is not None else None) for k, v in item.items()})
    return kept, rejections


def screen_narrative(text: str) -> tuple[str, list[str]]:
    """Remove sentences that assert negligence, causation, preventability or prescribe treatment."""
    warnings: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    kept = []
    for s in sentences:
        if UNSAFE_CONCLUSIONS.search(s):
            warnings.append("Removed a sentence asserting an unsupported conclusion")
            continue
        kept.append(s)
    return " ".join(kept).strip(), warnings
