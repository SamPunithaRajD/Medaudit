"""Page classification: deterministic keyword scoring, with optional LLM arbitration for ambiguous pages."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import PageType

KEYWORDS: dict[str, list[tuple[str, float]]] = {
    PageType.doctor_notes: [(r"progress\s*notes?", 3), (r"doctor'?s?\s*notes?", 3), (r"physician'?s?\s*notes?", 3),
                            (r"case\s*notes", 2), (r"clinical\s*notes", 2), (r"ward\s*round", 2), (r"\bo/e\b", 1),
                            (r"on\s*examination", 1), (r"\bplan\s*[:\-]", 1), (r"\badv(?:ice)?\s*[:\-]", 1), (r"\bRMO\b", 1)],
    PageType.nursing_notes: [(r"nursing\s*(?:notes?|record|care|assessment)", 3), (r"nurses'?\s*notes", 3),
                             (r"staff\s*nurse", 2), (r"intake\s*(?:/|and|&)?\s*output", 2), (r"hand\s*-?over", 1),
                             (r"\bshift\b", 1)],
    PageType.vitals: [(r"vital\s*signs?|vitals\s*chart", 3), (r"\bTPR\s*chart", 3), (r"observation\s*chart", 3),
                      (r"monitoring\s*chart", 2), (r"\bNEWS2?\b|\bMEWS\b", 2), (r"\bBP\b", 0.5), (r"\bSpO2\b", 0.5),
                      (r"\bpulse\b|\bHR\b", 0.5), (r"\bRR\b", 0.5), (r"\btemp", 0.5)],
    PageType.laboratory: [(r"laboratory|lab\s*report", 3), (r"ha?ematology|biochemistry|microbiology", 3),
                          (r"reference\s*(?:range|interval)", 2), (r"sample\s*collected|specimen", 2),
                          (r"\breported\s*(?:on|at)\b", 1), (r"\bCBC\b|\bLFT\b|\bRFT\b|\bKFT\b|\bABG\b", 1)],
    PageType.medication: [(r"medication\s*(?:chart|administration|record)", 3), (r"drug\s*(?:chart|administration)", 3),
                          (r"\bMAR\b", 2), (r"time\s*given", 2), (r"administered\s*by", 2)],
    PageType.prescription: [(r"prescription", 3), (r"treatment\s*(?:chart|advised|orders?)", 2),
                            (r"doctor'?s?\s*orders?", 2), (r"\bRx\b", 1)],
    PageType.icu: [(r"\b(?:MICU|SICU|ICU|CCU)\b", 1.5), (r"intensive\s*care", 2), (r"ventilator|vent\s*settings", 2),
                   (r"vasopressor|inotrope|noradrenaline|norepinephrine", 1.5), (r"\bFiO2\b|\bPEEP\b", 1.5)],
    PageType.emergency: [(r"emergency\s*(?:department|room|notes?)|casualty", 3), (r"triage", 2), (r"\bER\b", 1),
                         (r"brought\s*by", 1)],
    PageType.cpr: [(r"\bCPR\b|resuscitation\s*record", 3), (r"code\s*blue", 3), (r"cardiac\s*arrest", 2),
                   (r"\bROSC\b", 2), (r"chest\s*compressions?|defibrillat", 2)],
    PageType.radiology: [(r"radiology", 3), (r"x-?\s?ray|\bCT\b|\bMRI\b|ultrasound|\bUSG\b|\bHRCT\b", 2),
                         (r"\bfindings\s*[:\-]", 1), (r"radiograph", 2)],
    PageType.discharge_summary: [(r"discharge\s*summary", 4), (r"condition\s*(?:at|on)\s*discharge", 2),
                                 (r"date\s*of\s*discharge", 2), (r"discharge\s*advice", 2)],
    PageType.death_summary: [(r"death\s*summary", 4), (r"cause\s*of\s*death", 3), (r"declared\s*dead|time\s*of\s*death", 2),
                             (r"death\s*certificate|\bMCCD\b", 3)],
}
_COMPILED = {k: [(re.compile(p, re.I), w) for p, w in v] for k, v in KEYWORDS.items()}


@dataclass
class Classification:
    page_type: str
    method: str  # rule | rule_low_confidence | llm | none
    scores: dict[str, float]
    ambiguous: bool


def score_page(text: str) -> dict[str, float]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = "\n".join(lines[:5])
    scores: dict[str, float] = {}
    for cat, pats in _COMPILED.items():
        s = 0.0
        for pat, w in pats:
            body_hits = min(len(pat.findall(text)), 5)
            if body_hits:
                s += w * (1 + 0.15 * (body_hits - 1))
            if pat.search(header):
                s += w  # titles/headers weigh double
        if s:
            scores[str(cat)] = round(s, 2)
    return scores


def classify_rules(text: str) -> Classification:
    if not text.strip():
        return Classification(PageType.unknown, "none", {}, False)
    scores = score_page(text)
    if not scores:
        return Classification(PageType.unknown, "none", {}, True)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, top_s = ranked[0]
    second_s = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_s >= 3 and top_s - second_s >= 1.5:
        return Classification(top, "rule", scores, False)
    return Classification(top if top_s >= 1.5 else PageType.unknown, "rule_low_confidence", scores, True)


def classify(text: str, llm_classifier=None) -> Classification:
    """`llm_classifier(text, candidates) -> str|None` is consulted only for ambiguous pages."""
    result = classify_rules(text)
    if result.ambiguous and llm_classifier is not None and text.strip():
        candidates = [str(c) for c in PageType]
        try:
            label = llm_classifier(text, candidates)
        except Exception:
            label = None
        if label in candidates:
            return Classification(label, "llm", result.scores, False)
    return result
