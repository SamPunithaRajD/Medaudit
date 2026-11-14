# MedAudit AI 2.0 — Backend

FastAPI backend for multi-agent case-sheet analysis and mortality-audit decision support. It ingests
scanned/digital case sheets (PDF or ZIP of images/PDFs), extracts page text, classifies pages, extracts
structured clinical data with source provenance, reconstructs a timeline, evaluates **hospital-approved,
versioned protocol rules**, verifies evidence, prepares a transparent preliminary preventability evidence
summary, and produces versioned, encrypted audit reports for human review.

> **Decision support only.** Outputs assist qualified reviewers and the mortality audit committee. The
> system never establishes negligence, causation or final preventability. Its preliminary category can
> only be `possibly_preventable` or `undetermined`; `preventable`/`unavoidable` are reserved for humans.
> Implementing these security controls does **not** by itself make a deployment compliant with HIPAA,
> NABH, ICMR or any other regulation.

## Quick start (Windows, PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python scripts\generate_secrets.py   # paste output into .env
# edit DATABASE_URL in .env (see documentation/windows-setup.md for PostgreSQL)
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python scripts\create_admin.py --login-id ADM001 --email you@hospital.local --name "Your Name"
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000      # terminal 1: API
.\.venv\Scripts\python -m app.workers.run_worker               # terminal 2: worker
```

API docs: http://localhost:8000/docs · health: http://localhost:8000/health

Optional demo data (synthetic patient, demo users, illustrative protocol):
`.\.venv\Scripts\python scripts\seed_demo.py`

Tests: `.\.venv\Scripts\python -m pytest` (uses a temporary SQLite DB; set `TEST_DATABASE_URL` to run against PostgreSQL).

## Optional local components

| Component | Needed for | Without it |
|---|---|---|
| **Ollama** + `llama3.2:3b` | LLM page classification for ambiguous pages, validated LLM extraction, report/assistant phrasing | Deterministic extraction and templates are used; job warnings say so |
| **Tesseract OCR** | Scanned images / image-only PDFs | Digital PDFs still work (text layer); image pages are flagged for manual transcription |

Handwriting: Tesseract is not reliable for handwriting. Low-confidence pages are flagged
`possible_handwriting_or_illegible`. A handwriting-capable recogniser can be plugged in via `OCR_EXTRA_PROVIDER`.

## Layout

```
app/
  api/v1/               REST routers (auth, cases, documents, timeline, audit, preventability,
                        recommendations, reviews, analytics, protocols, assistant, admin, system)
  core/                 settings, database, logging (redacting), error types
  models/               SQLAlchemy 2 models (31 tables)
  schemas/              Pydantic request/response models
  security/             AES-256-GCM, Argon2id sessions, RBAC/case access, audit log chain, de-identification, rate limit
  document_intelligence/ ingestion (validation, ZIP safety, page sources), preprocessing (OpenCV),
                        ocr (pluggable providers), classification, extraction (timestamps, patterns), validation
  ai_engine/            ollama client, prompts, LLM output validators, 11 agents, orchestrator, PDF reports
  audit_engine/         timeline builder, event correlation, protocol rule engine, preventability, recommendations
  analytics/            de-identified, suppressed aggregate metrics
  services/             upload, processing pipeline, storage, serializers, assistant, counterfactual
  workers/              persistent DB job queue + worker
  evaluation/           OCR/extraction/timeline/agreement metrics
migrations/             Alembic (incl. PostgreSQL append-only trigger for audit_logs)
scripts/                generate_secrets, create_admin, seed_demo, rotate_keys, evaluate
tests/                  unit, integration, security tests
```

Documentation: see `../documentation/` — architecture, API contract, schema, agents, pipeline, security,
clinical validation, Windows setup and frontend integration.

## Accounts

Doctor IDs are provisioned by administrators and activated by doctors with a one-time code; admins and doctors get separate portals (`/admin`, `/doctor`). See `../documentation/authentication-architecture.md` for the design, permission matrix, activation workflow and secure admin bootstrap.
