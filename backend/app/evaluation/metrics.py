"""Evaluation metrics for OCR, extraction, timeline ordering, evidence support and reviewer agreement.

These functions compute metrics from gold-standard annotations prepared on DE-IDENTIFIED data by
qualified experts. No accuracy figures are claimed by this project; run scripts/evaluate.py on your
own annotated set (format documented in documentation/clinical-validation.md).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence
from itertools import combinations


def _levenshtein(a: Sequence, b: Sequence) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    return _levenshtein(reference, hypothesis) / max(len(reference), 1)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = reference.split()
    return _levenshtein(ref, hypothesis.split()) / max(len(ref), 1)


def precision_recall_f1(gold: set[Hashable], predicted: set[Hashable]) -> dict[str, float | int]:
    tp = len(gold & predicted)
    p = tp / len(predicted) if predicted else 0.0
    r = tp / len(gold) if gold else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4), "tp": tp,
            "fp": len(predicted - gold), "fn": len(gold - predicted)}


def pairwise_order_accuracy(gold_order: Sequence[Hashable], predicted_order: Sequence[Hashable]) -> float | None:
    """Fraction of event pairs (present in both) placed in the same relative order (Kendall-style)."""
    pos = {e: i for i, e in enumerate(predicted_order)}
    common = [e for e in gold_order if e in pos]
    pairs = list(combinations(common, 2))
    if not pairs:
        return None
    return round(sum(1 for a, b in pairs if pos[a] < pos[b]) / len(pairs), 4)


def evidence_support_rate(statuses: Sequence[str]) -> float | None:
    return round(sum(1 for s in statuses if s == "verified") / len(statuses), 4) if statuses else None


def cohens_kappa(rater_a: Sequence[Hashable], rater_b: Sequence[Hashable]) -> float | None:
    if len(rater_a) != len(rater_b) or not rater_a:
        return None
    n = len(rater_a)
    po = sum(1 for a, b in zip(rater_a, rater_b) if a == b) / n
    ca, cb = Counter(rater_a), Counter(rater_b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0
