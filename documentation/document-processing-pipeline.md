# Document processing pipeline

## 1. Upload (synchronous, `POST /cases/upload`)

1. Stream read with a hard byte cap (`MAX_UPLOAD_SIZE_MB`; `Content-Length` pre-check in middleware) → 413.
2. Extension and magic-byte signature must agree (`%PDF-` / `PK\x03\x04`) → 415.
3. Structure check: PDF parse (password-protected rejected) or ZIP inspection:
   path traversal/absolute paths/drive letters, symlinks, encrypted members, nested archives, member count,
   total uncompressed size, compression ratio (ZIP-bomb), member signature (JPG/PNG/PDF only) → 422 `INVALID_ARCHIVE`.
4. Page count ≤ `MAX_PDF_PAGES` → 422 `TOO_MANY_PAGES`.
5. SHA-256; duplicate of a file in a case the user can access → 409 `DUPLICATE_UPLOAD`.
6. Original encrypted (AES-256-GCM, AAD = document id) to `storage/documents/<id>/original.enc`.
7. Case, document, job and stages created; job queued; **202** returned with `status_url`.

## 2. Worker stages (JobStatus → stage key)

| JobStatus | Stage key | Work |
|---|---|---|
| validating | ingest | decrypt + authenticate original, verify SHA-256 (tamper → `DOCUMENT_INTEGRITY`) |
| ingesting | ingest | enumerate pages (ZIP members in natural order; PDFs inside ZIPs expanded); page rows created |
| preprocessing / extracting | preprocess, ocr | page by page, in batches of `PAGE_BATCH_SIZE`: probe text layer → rasterise only if needed → OpenCV preprocessing (grayscale, upscale if low-res, orientation from Tesseract OSD, denoise, CLAHE contrast, deskew, adaptive threshold) → OCR provider chain → exact-duplicate detection by hash → per-page result, retries and failure isolation; progress committed per batch; cancellation checked between batches |
| classifying | classify | keyword scoring (headers weigh double); ambiguous pages → LLM with fixed label set (if available) |
| analyzing | extract_entities, analyze | agents 1–8 |
| building_timeline | timeline | timeline + rule evaluation |
| verifying_evidence | verify_evidence | evidence verification, preliminary preventability, recommendations |
| generating_report | report | versioned encrypted report |

Case `processing_state`: uploaded → queued → processing → ocr_completed → ai_analysis_completed →
verification_pending (awaiting human review) → completed (committee approved) | failed.

## 3. OCR providers

`OCR_PROVIDERS=pdf_text,tesseract` (order matters). `pdf_text` uses the embedded text layer (no confidence
exists, so `null`). `tesseract` reports engine word confidences (`heuristic`). Pages with mean confidence under
`OCR_LOW_CONFIDENCE_THRESHOLD`, or >35% low-confidence words, are flagged (`possible_handwriting_or_illegible`)
and `needs_manual_review=true`. Pages with no usable provider are kept, flagged, and reported.

Add a handwriting-capable recogniser by subclassing `app.document_intelligence.ocr.base.OCRProvider`
(`available()`, `supports()`, `recognize()`) and setting `OCR_EXTRA_PROVIDER=package.module:ClassName`.

## 4. Extraction rules

- Values recognised only when labelled (e.g. `HR 112`, `BP 88/54`, `Lactate: 3.8 mmol/L (Ref 0.5-2.2) H`).
- Plausibility ranges catch OCR errors (marked `uncertain`); they are not clinical thresholds.
- Lab abnormality only from a documented flag or documented reference range (basis recorded).
- Times: see `backend-api-contract.md` §1. Nothing is inferred across pages.
- Each item: status `documented | normalized | missing | uncertain`, source page and excerpt.
