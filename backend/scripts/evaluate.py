"""Compute evaluation metrics from an expert-annotated, de-identified gold file.

    python scripts/evaluate.py gold.json predictions.json

gold.json / predictions.json (same shape):
{
  "pages":       {"<doc>:<page>": "text"},                       # OCR reference / output
  "entities":    [["vital", "heart_rate", "2026-10-14T09:15", "112"], ...],
  "medications": [["piperacillin", "4.5 g", "IV", "administration"], ...],
  "timeline":    ["event-key-1", "event-key-2", ...],             # chronological order
  "evidence_statuses": ["verified", "disputed", ...],             # predictions only
  "reviewer_a":  ["accept", "reject", ...], "reviewer_b": [...]   # gold only: expert labels per finding
}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import metrics as M  # noqa: E402


def main(gold_path: str, pred_path: str) -> None:
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    pred = json.loads(Path(pred_path).read_text(encoding="utf-8"))
    out: dict = {}
    pages = [k for k in gold.get("pages", {}) if k in pred.get("pages", {})]
    if pages:
        out["ocr_cer_mean"] = round(sum(M.character_error_rate(gold["pages"][k], pred["pages"][k]) for k in pages) / len(pages), 4)
        out["ocr_wer_mean"] = round(sum(M.word_error_rate(gold["pages"][k], pred["pages"][k]) for k in pages) / len(pages), 4)
    for key in ("entities", "medications"):
        if key in gold:
            out[key] = M.precision_recall_f1({tuple(x) for x in gold[key]}, {tuple(x) for x in pred.get(key, [])})
    if "timeline" in gold:
        out["timeline_pairwise_order_accuracy"] = M.pairwise_order_accuracy(gold["timeline"], pred.get("timeline", []))
    if "evidence_statuses" in pred:
        out["evidence_support_rate"] = M.evidence_support_rate(pred["evidence_statuses"])
    if "reviewer_a" in gold and "reviewer_b" in gold:
        out["expert_agreement_kappa"] = M.cohens_kappa(gold["reviewer_a"], gold["reviewer_b"])
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
