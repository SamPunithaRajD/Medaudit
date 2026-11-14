# Clinical validation

**No accuracy, precision or agreement figures are claimed.** The automated tests use one synthetic,
digitally-generated case sheet and prove software behaviour only, not clinical performance.

## What must be validated before clinical use

| Component | Metric | Tool |
|---|---|---|
| OCR (per scan type, printed vs handwritten) | character / word error rate | `evaluation.metrics.character_error_rate`, `word_error_rate` |
| Page classification | per-class precision/recall | `precision_recall_f1` |
| Entity extraction (vitals, labs, patient fields) | precision/recall/F1 on (type, field, time, value) tuples | `precision_recall_f1` |
| Medication extraction | P/R/F1 on (drug, dose, route, kind) | same |
| Timeline ordering | pairwise order accuracy | `pairwise_order_accuracy` |
| Evidence support | share of evidence references verified | `evidence_support_rate` |
| Finding usefulness | reviewer accept/reject/amend rates (from `finding_reviews`) | SQL / analytics |
| Expert agreement | Cohen's kappa between independent reviewers | `cohens_kappa` |

Run: `python scripts/evaluate.py gold.json predictions.json` (format in the script docstring).

## Protocol

1. Use de-identified records (`security/anonymization.py` helps but manual verification of de-identification is required) under institutional ethics/governance approval.
2. Two qualified clinicians annotate independently; adjudicate disagreements; compute kappa.
3. Stratify by department, document quality and handwriting share; report confidence intervals.
4. Validate each hospital protocol version with its owners before approval; record approvers (`protocol_versions.approved_by`).
5. Re-validate after changes to the OCR provider, LLM model, extraction patterns or protocols.
6. The preliminary preventability framework (`MEDAUDIT-PRELIM-PREVENTABILITY` v1.0.0) is an evidence-organisation
   index with configurable weights. It has not been validated against committee outcomes and must not be
   presented as a probability.

## Built-in behaviour reviewers should know

- No clinical threshold is hard-coded. Without an approved protocol, only documentation-integrity checks run, and the report says so.
- The seeded "DEMO monitoring & escalation protocol" contains illustrative thresholds only; replace it.
- "Missing signature marker" findings are informational: handwritten signatures are often invisible to text extraction.
- Absence of documentation is reported as absence of documentation, not as absence of care.
