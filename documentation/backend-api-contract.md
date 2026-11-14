# MedAudit AI 2.0 · Backend API contract (implemented)

Version 1.0 · 13 November 2026. This documents what the FastAPI backend in `backend/` actually implements.
It follows `frontend-backend-contract.md` (the integration authority); deviations and extensions are listed
in §5. Live, machine-readable schemas: `GET /openapi.json` and `/docs` (disabled when `APP_ENV` is staging/production).

## 1. Conventions

- Base path `/api/v1`; IDs are opaque prefixed strings (`CASE-…`, `DOC-…`, `JOB-…`, `FND-…`, `EVT-…`, `REC-…`).
- Timestamps: RFC 3339 UTC with `Z`. Case-sheet wall-clock times are interpreted in `HOSPITAL_TIMEZONE`.
  Every clinical time carries a precision (`second|minute|hour|date|unknown`) and a `timestamp_basis`
  (`explicit` = written on the same line; `page_context_date` = time on the line, date written earlier on the same
  page — treated as uncertain; `none`). Missing times are `null`, never inferred.
- Lists: `{"items": [...], "pagination": {page, page_size, total_items, total_pages, next_page, previous_page}}`;
  `page` default 1, `page_size` default 25, max 100.
- Errors: `{"error": {"code", "message", "details": [{field, issue}], "request_id"}, "message"}` (top-level
  `message` duplicates `error.message` for the current frontend client). Codes used: `VALIDATION_ERROR` 422,
  `UNAUTHENTICATED`/`INVALID_CREDENTIALS` 401, `FORBIDDEN`/`COMMITTEE_ONLY`/`CSRF_REJECTED` 403,
  `CASE_NOT_FOUND` and other `*_NOT_FOUND` 404, `DUPLICATE_UPLOAD`/`REVIEW_CONFLICT`/`CASE_CONFLICT`/`JOB_ACTIVE` 409,
  `FILE_TOO_LARGE` 413, `UNSUPPORTED_MEDIA_TYPE` 415, `INVALID_ARCHIVE`/`INVALID_DOCUMENT`/`TOO_MANY_PAGES` 422,
  `RATE_LIMITED` 429 (+`Retry-After`), `INTERNAL_ERROR` 500, `DOCUMENT_INTEGRITY`/`DOCUMENT_UNAVAILABLE` 503.
- Inaccessible cases/documents/jobs return **404** (not 403) to avoid existence leaks.
- Authentication: HttpOnly session cookie `medaudit_session` (browser) or `Authorization: Bearer <token>`
  (native clients send `X-MedAudit-Client: native` at login to receive the token). The actor of every write
  comes from the session, never the request body.
- Confidence: `null` with `confidence_method: "unavailable"` unless a real method exists. OCR-engine scores are
  exposed as `heuristic`. Deterministic rule findings have `confidence: null`.

## 2. Enumerations

Contract enums are used as-is: CaseStatus, ReviewStatus, FindingCategory, PreventabilityCategory
(`undetermined` = "insufficient evidence"), RecommendationStatus, DeathLocation, JobStatus, Role.
Additional backend enums:
- `processing_state` (case): `uploaded, queued, processing, ocr_completed, ai_analysis_completed, verification_pending, completed, failed`.
- Severity adds `informational` (used only for built-in documentation observations such as "no signature marker").
- Finding `verification_status`: `unverified, verified, partially_verified, unsupported, absence_based`.
- Medication `record_kind`: `order, administration, change, unspecified`.
- Page type: `doctor_notes, nursing_notes, vitals, laboratory, medication, prescription, icu, emergency, cpr, radiology, discharge_summary, death_summary, unknown`.

## 3. Endpoints

| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/health` | public | `{"status":"ok"}` |
| GET | `/system/status` | any user | DB, encryption, OCR providers, worker mode |
| GET | `/system/ollama-status` | any user | reachable, model_available, local-only flag |
| POST | `/auth/login` | public (rate-limited) | `{email,password}` → `{user, session:{expires_at[,token]}}` + cookie |
| POST | `/auth/logout` | user | revokes server session |
| GET | `/auth/me` | user | user incl. `permissions[]` |
| POST | `/auth/change-password` | user | revokes all sessions |
| GET/POST | `/admin/users` | admin | create sets `must_change_password` |
| PATCH | `/admin/users/{id}` | admin | role/status changes revoke sessions |
| POST | `/admin/users/{id}/revoke-sessions` | admin | |
| GET | `/admin/audit-logs`, `/admin/audit-logs/verify` | admin | hash-chain verification |
| POST | `/cases/upload` | doctor, nurse, auditor, admin | multipart `file` (+`department`, `patient_ref`, `metadata` JSON; `Idempotency-Key`). **202** contract body |
| GET | `/cases` | case.read | filters `status, review_status, department, from, to, q`; `sort=created_at:desc` |
| GET | `/cases/{id}` | case.read | `{case, document_count, finding_count, recommendation_count, latest_job}` |
| PATCH | `/cases/{id}` | doctor, auditor, admin | committee-only statuses enforced; `expected_updated_at` → 409 |
| POST/DELETE | `/cases/{id}/assignments[/{user_id}]` | admin | case-level access control |
| GET | `/cases/{id}/status` | case.read | contract status + `progress{completed_pages,total_pages,percentage}`, `processing_state` |
| POST | `/cases/{id}/process` | case.process | reprocess → **202**; 409 if a job is active |
| GET | `/jobs/{id}`, `/jobs/{id}/agents` | case.read | per-agent status, attempts, LLM use, errors |
| POST | `/jobs/{id}/cancel` | case.process | queued: immediate; running: between pages/agents |
| GET | `/cases/{id}/documents` | document.read | contract items + `sha256`, `download_url` |
| GET | `/documents/{id}` | document.read | metadata + paginated page metadata (type, OCR quality, duplicates, manual-review reasons) |
| GET | `/documents/{id}/pages/{n}` | document.read | page metadata + finding evidence on that page |
| GET | `/documents/{id}/pages/{n}/text` | document.read | `include_words=true` adds normalised word boxes |
| GET | `/documents/{id}/pages/{n}/preview` | document.read | PNG rendered on demand, `Cache-Control: no-store` |
| GET | `/documents/{id}/download` | doctor, auditor, committee, admin | decrypted original, integrity-checked, audit-logged |
| GET | `/evidence/{id}` | document.read | |
| GET | `/cases/{id}/timeline` | case.read | `type` (repeatable), `from`, `to`, `q`, `sort=occurred_at:asc|desc`, `include_untimed` |
| GET | `/cases/{id}/vitals` | case.read | `measurement` (repeatable); one canonical unit per type |
| GET | `/cases/{id}/medications` | case.read | `record_kind` |
| GET | `/cases/{id}/laboratory` | case.read | `test_code`, `abnormal_only` |
| GET | `/cases/{id}/audit-report` | case.read | contract body + `report_id, report_version, generated_at, sections{1..16}, source_documents, provenance`; `?version=` |
| POST | `/cases/{id}/audit-report/regenerate` | case.update | deterministic regeneration from reviewed data |
| GET | `/cases/{id}/audit-report/versions` | case.read | |
| GET | `/cases/{id}/audit-report/export?format=pdf|json` | report.export | audit-logged |
| GET | `/cases/{id}/findings` | case.read | `category, severity, review_status, verification_status, sort` |
| GET | `/findings/{id}` | case.read | finding + immutable review history |
| POST | `/findings/{id}/review` | comment: all clinical roles; accept/reject/amend: doctor, committee, admin | contract body/response; 409 `REVIEW_CONFLICT` |
| GET | `/cases/{id}/preventability` | case.read | preliminary, clinical review, committee decision kept separate |
| POST | `/cases/{id}/preventability/review` | clinical_review: doctor/committee/admin; committee_decision: committee/admin | `{decision_type, category, rationale, contributing_factors, supporting_finding_ids}` |
| GET | `/cases/{id}/recommendations` | case.read | |
| POST/PATCH | `/recommendations[/{id}]` | recommendation.manage | returns `{recommendation}` |
| GET/POST | `/actions` | recommendation.manage | corrective actions = tracked recommendations |
| PATCH | `/actions/{id}` | recommendation.manage | validated status transitions; history recorded |
| GET | `/actions/{id}/history` | recommendation.manage | append-only status history |
| GET/POST | `/reviews`, GET `/reviews/{id}` | review.manage | `mine=true` filter |
| POST | `/reviews/{id}/comments` | review.manage | |
| POST | `/reviews/{id}/decision` | doctor, committee, admin | `approve|reject|request_changes|escalate_to_committee` |
| GET | `/analytics/overview` | doctor, auditor, committee, admin | contract body + `preliminary_preventability_distribution`, `unreviewed_findings`, `min_cell_size` |
| GET | `/analytics/departments`, `/trends`, `/common-findings` | same | confirmed findings only; suppression applied |
| GET/POST | `/protocols` | read: all; write: admin | rules validated against the rule schema |
| GET | `/protocols/{id}` | protocol.read | versions + current approved version |
| POST | `/protocols/{id}/versions`, `/versions/{vid}/approve` | admin | approving retires the previous approved version |
| POST | `/protocols/{id}/compare` | protocol.read | dry run `{case_id, version_id?}`; names exact version; nothing persisted |
| POST | `/assistant/message` | assistant.use | contract body; adds `case_id`, `message.intent`, `message.llm_used` |
| GET | `/assistant/conversations[/{id}]` | assistant.use | private to author + current case access |
| POST/GET | `/cases/{id}/counterfactual` | counterfactual.use | always `hypothetical: true` |
| POST | `/counterfactual/{id}/comments` | counterfactual.use | reviewer comments |

## 4. Processing response example (real shape)

```json
{"case_id":"CASE-6F005ECAEA3E","job_id":"JOB-7F3918B54881","status":"extracting","progress_percent":31.4,
 "stage":"ocr","stages":[{"key":"ingest","label":"Ingest document","status":"completed","started_at":"…","completed_at":"…","detail":{"pages":9}}, …],
 "progress":{"current_stage":"ocr","completed_pages":4,"failed_pages":0,"total_pages":9,"percentage":31.4},
 "processing_state":"processing","error":null}
```

## 5. Inconsistencies found and how they were resolved

| # | Observation | Resolution |
|---|---|---|
| 1 | `src/data.js` mock shapes (e.g. `patient: "Name"`, `time: "14 Oct · 08:42"`, `page`, `category: "Medical management"`) differ from the contract (`patient:{display_name,…}`, ISO `occurred_at`, `evidence[]`, enum categories). `App.jsx` renders mock shapes. | Backend follows the contract. The UI needs adapters in `src/api/services.js` before `VITE_USE_MOCK_DATA=false` (see frontend-integration.md). |
| 2 | `src/api/client.js` axios instance does not set `withCredentials: true`. | Required for cookie sessions across origins (5173 → 8000). |
| 3 | Client reads `err.response.data.message`; contract errors use `error.message`. | Backend returns both. |
| 4 | Contract upload limit recommends 100 MiB; backend brief suggested 500 MB. | Default `MAX_UPLOAD_SIZE_MB=100`, configurable. |
| 5 | Brief lists "Insufficient Evidence"; contract uses `undetermined`. | `undetermined` (documented as insufficient evidence). |
| 6 | Brief recommendation priorities High/Medium/Low; contract uses Severity. | Contract Severity values. |
| 7 | Brief lists severity `informational`; contract does not. | Added as an extension; frontend should render it as lowest severity. |
| 8 | Brief lists `/actions` separately from recommendations; contract returns `{recommendation}` from `/actions`. | Actions are tracked recommendations with a history table. |
| 9 | Contract has no login/me endpoints; frontend role selection is demonstrative. | `/auth/*` implemented; role comes from the server session. |
| 10 | `recommendationsApi.list` and `timelineApi` use a `localStorage` case id fallback `MA-2026-0148`. | Real IDs are `CASE-…`; the UI must pass the selected case id. |
| 11 | `analyticsApi.overview` mock returns `{total, reviewed}`. | Backend returns the contract overview shape. |
| 12 | Brief: `GET /documents/{id}/pages/{n}` = page; contract: previews requested individually. | `/pages/{n}` returns metadata; `/pages/{n}/preview` returns PNG. |


## 6. Accounts, doctor workspace and administration (v2.0)

All bodies are JSON unless noted. Cookie-authenticated POST/PUT/PATCH/DELETE must send `X-CSRF-Token`
(value from `csrf_token` in the login / activation / `GET /auth/me` response). Missing or wrong token → 403 `CSRF_REJECTED`.

### Authentication (`/api/v1/auth`)

| Method | Path | Body → response |
|---|---|---|
| POST | `/login` | `{login_id, password, remember_me?}` (`email` accepted as a legacy alias) → `{user, session:{id, expires_at, remember[, token]}, csrf_token, redirect_to}`. 401 `INVALID_CREDENTIALS` (generic, also for pending/unknown/locked), 403 `ACCOUNT_SUSPENDED` / `ACCOUNT_INACTIVE` (only after a correct password), 429 |
| POST | `/doctor/activate` | `{doctor_id, email, activation_code, password, confirm_password}` → 201, same as login (session started). 422 `ACTIVATION_INVALID` (generic) or `VALIDATION_ERROR` (password policy) |
| POST | `/password/forgot` | `{login_id}` → 202 `{message}` (always identical) |
| POST | `/password/reset` | `{login_id, reset_code, new_password, confirm_password}` → `{status}`; 422 `RESET_INVALID`. Revokes all sessions |
| POST | `/logout` | → `{status}` |
| GET | `/me` | → `{user, csrf_token, session, preferences}` |
| POST | `/password/change` | `{current_password, new_password, confirm_password}` → `{status, other_sessions_revoked}` |
| GET | `/sessions` | → `{items:[{id, created_at, last_activity_at, expires_at, remember, user_agent, current, active}]}` |
| DELETE | `/sessions/{session_id}` | revoke one of your sessions |
| POST | `/sessions/revoke-others` | → `{revoked}` |
| GET | `/activity` | paginated own `auth.*` events |

`user` = `{id, login_id, email, display_name, role, department, designation, status, is_activated, must_change_password, last_login_at, home, permissions[]}`. `home`/`redirect_to` is `/admin` for admins, `/doctor` for doctors, `/` otherwise.

### Administration (`/api/v1/admin`, admin only)

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboard` | `{total_doctor_ids, activated_accounts, pending_activations, active_doctors, suspended_accounts, deactivated_accounts, archived_accounts, password_reset_requests, failed_logins_7d, logins_7d, recent_registrations[], recent_logins[], security_events[]}` |
| GET | `/doctors` | `q, status=all|active|pending_activation|suspended|deactivated|archived, department, sort=field:dir (doctor_id, full_name, department, created_at, last_login_at, status), page, page_size` → paginated Doctor + `departments[]` |
| POST | `/doctors` | `{doctor_id?, full_name, email, phone?, department, designation?, employee_ref?, valid_until?}` → 201 `{doctor, activation:{activation_code, expires_at, activation_url, notice}}`; 409 `DOCTOR_CONFLICT` with `details[].field` |
| GET | `/doctors/{doctor_id}` | `{doctor, activation_history[], active_sessions[], security_events[], administrative_changes[]}` |
| PATCH | `/doctors/{doctor_id}` | editable profile fields + `expected_updated_at` (409 on stale). Role and Doctor ID are not editable |
| POST | `/doctors/{id}/suspend`, `/deactivate` | `{reason}`; sessions revoked |
| POST | `/doctors/{id}/reactivate` | `{reason?}` |
| DELETE | `/doctors/{id}?reason=` | archive (soft) |
| POST | `/doctors/{id}/resend-activation` | new code, previous revoked |
| POST | `/doctors/{id}/reset-activation` | `{reason, confirm_doctor_id}` → back to pending + new code |
| POST | `/doctors/{id}/password-reset` | `{revoke_sessions?}` → `{password_reset:{reset_code, expires_at, reset_url}}` |
| GET / DELETE | `/doctors/{id}/sessions` | list / revoke all |
| GET | `/doctors/export` | CSV (formula-injection safe) |
| GET | `/doctors/import-template` | CSV template |
| POST | `/doctors/import?commit=false|true` | multipart `file`; preview with per-row errors; commit is atomic and only when every row is valid; returns codes once |
| GET / PATCH | `/settings` | hospital authentication policy |
| GET | `/audit-logs` | `action, category, outcome, doctor_id, target_user_id, actor_id, case_id` filters |
| GET | `/audit-logs/verify` | hash-chain verification |
| GET/POST/PATCH | `/users…` | non-doctor staff accounts (doctors must use provisioning) |

Doctor = `{user_id, doctor_id, full_name, email, phone, department, designation, employee_ref, valid_until, account_status, status_reason, is_activated, activation_status (activated|pending|code_expired|no_active_code), activated_at, password_reset_requested_at, created_at, updated_at, last_login_at, created_by}`.

### Doctor workspace (`/api/v1/doctor`, doctor role only; every query scoped to the signed-in doctor)

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboard` | own totals, recent documents, recent patient activity, pending reviews, recent reports |
| GET / PATCH | `/profile` | PATCH accepts only `{phone, contact_info}`; anything else → 422 |
| GET / PATCH | `/preferences` | `{theme (light|dark|system), language, notification_preferences{}, dashboard_preferences{}}` (merged) |
| GET | `/patients` | `q, case_status, scope=all|owned|shared` |
| POST | `/patients` | `{patient_ref, display_name?, age_years?, gender?, department?, notes?}`; 409 `PATIENT_EXISTS` |
| GET / PATCH | `/patients/{id}` | detail incl. cases and shares (owner); PATCH needs owner/contribute |
| POST | `/patients/{id}/documents` | multipart `file` → 202 upload contract (case linked to the patient) |
| GET | `/patients/{id}/cases` | analysis history |
| POST / DELETE | `/patients/{id}/share[/{doctor_id}]` | `{doctor_id, access_level: read|contribute}` (owner only) |
| GET | `/cases` | own + assigned + shared-patient cases; `status, review_status, q` |

### Case access changes

- `POST /cases/upload` accepts an optional `patient_id` form field (must be your patient).
- Administrators no longer see unassigned cases. `POST /cases/{id}/assignments` manages access without granting it to the admin, and returns `{case_id, assignments[]}`.
- `POST /cases/{id}/emergency-access` `{reason ≥20 chars, duration_minutes}` → `{case_id, expires_at, duration_minutes}` (admin, committee member; audited; time-limited).
