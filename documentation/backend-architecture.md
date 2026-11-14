# Backend architecture

## Principles

1. Evidence first: every extracted value, event and finding carries document id, page and source excerpt.
2. Deterministic before generative: measurable checks are code + approved protocol rules; the LLM never decides a deviation.
3. LLM output is untrusted: JSON-schema constrained, evidence-quote verified, unsafe-conclusion screened, optional.
4. Human oversight: findings start `pending`; committee decisions are separate records and report sections.
5. Local-first: Ollama on loopback/private network only; no external AI integration exists.

## Components

```
React (Vite)  ──HTTPS/cookie──►  FastAPI (app/main.py)
                                   ├─ middleware: request id, CSRF origin check, body limit, security headers, CORS
                                   ├─ api/v1/*   (thin routers: validation, authz, audit logging)
                                   ├─ services/  (upload, pipeline, serializers, assistant, counterfactual, storage)
                                   └─ PostgreSQL (SQLAlchemy 2 / Alembic)
Worker (app.workers.run_worker) ── claims jobs from processing_jobs (FOR UPDATE SKIP LOCKED)
   └─ Pipeline: validate → ingest → preprocess+OCR (page by page) → classify → agents → timeline+rules
                → evidence verification → preventability → recommendations → report
Encrypted file storage (storage/, AES-256-GCM)       Ollama (localhost:11434, optional)
```

## Layering

| Layer | Package | Responsibility |
|---|---|---|
| API | `app/api` | request/response, permissions, audit events |
| Services | `app/services` | use-case orchestration and transactions |
| Document intelligence | `app/document_intelligence` | file safety, rendering, preprocessing, OCR providers, classification, deterministic extraction |
| AI engine | `app/ai_engine` | Ollama client, prompts, validators, agents, orchestration, PDF |
| Audit engine | `app/audit_engine` | timeline, correlation, protocol rules + built-in documentation checks, preventability framework, recommendations |
| Security | `app/security` | crypto, sessions, RBAC, audit log, de-identification, rate limiting |
| Data | `app/models` | normalised schema |

Gap detection lives in the rule engine (`audit_engine/clinical_rules/engine.py`) and the Documentation agent.
Access-scoped queries live in `security/access_control.py` (`accessible_cases_query`, `get_case_for_user`).

## Background processing

- Queue: `workers/queue.py` (`JobQueue` interface, `DatabaseJobQueue` default). Replaceable by Redis/Celery
  by implementing `enqueue/claim/cancel`.
- Persistence: job, per-stage (`job_stages`), per-page (`document_pages.status/attempts/error`), per-agent (`agent_executions`).
- Retries: page (`PAGE_MAX_RETRIES`), agent (`AGENT_MAX_RETRIES`), job (`JOB_MAX_ATTEMPTS`; retryable failures requeue).
- Recovery: a stale job (no heartbeat for `JOB_LOCK_TIMEOUT_SECONDS`) is reclaimed; completed pages and unchanged agents are skipped.
- Concurrency: `WORKER_CONCURRENCY` threads per worker process; run several worker processes on PostgreSQL. Agents
  inside a job run sequentially to respect local-LLM memory limits.
- `WORKER_MODE=embedded` runs a worker thread inside the API process for development only.

## Known limitations

- Rate limiter is in-process; behind multiple API processes use a shared store.
- Table-formatted vitals without per-value labels (e.g. column headers + bare numbers) are not parsed by rules;
  they need an LLM or a table-aware extractor.
- Rendering decrypts the whole original in worker memory (bounded by `MAX_UPLOAD_SIZE_MB`).
- Signatures are usually handwritten; "missing signature marker" is an informational, uncertain observation.
