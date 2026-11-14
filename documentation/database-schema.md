# Database schema

PostgreSQL via SQLAlchemy 2; migrations in `backend/migrations` (`alembic upgrade head`). JSON columns are
`JSONB` on PostgreSQL. All timestamps are `timestamptz` (UTC). IDs are prefixed opaque strings.
Columns marked 🔒 are AES-256-GCM encrypted at the application layer.

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `roles` | role catalogue (seeded) | `name` PK |
| `users` | accounts | `email` unique, `role`→roles, `password_hash` (Argon2id), `status`, lockout fields, soft delete |
| `user_sessions` | server-side sessions | `token_hash` unique (HMAC of token), `expires_at`, `last_seen_at`, `revoked_at` |
| `patient_cases` | audit case | 🔒`patient_name`, 🔒`patient_identifier`, `status`, `review_status`, `processing_state`, `field_provenance`, soft delete; indexes on status/department/patient_ref |
| `case_assignments` | case-level access | unique (`case_id`,`user_id`) |
| `documents` | uploaded file | `sha256` (indexed, dedupe), `storage_key`, `key_version`, `metadata_json` |
| `document_pages` | page registry | unique (`document_id`,`page_number`); source member/page index, `sha256`, `duplicate_of_page`, status/attempts/error, `page_type`, manual-review flags |
| `ocr_results` | page text | 🔒`text`, 🔒`words_json`, provider, mean confidence + method, handwriting flag |
| `processing_jobs` | persistent queue | status, attempts, progress, lock/heartbeat, cancel flag, error |
| `job_stages` | stage progress | unique (`job_id`,`key`) |
| `agent_executions` | agent runs | unique (`job_id`,`agent_name`); status, attempts, duration, LLM use, input hash, summary |
| `extracted_entities` | patient fields, decisions, documentation issues | `status` documented/normalized/missing/uncertain, method, source |
| `clinical_events` | timeline | `occurred_at` + precision + basis, type/subtype, flags, verification |
| `vital_observations` | vitals | measurement type, value, canonical unit, original unit |
| `medication_records` | medication orders/administrations | dose, route, frequency, kind, times + precision |
| `laboratory_records` | labs | value, unit, documented reference range, abnormal flag + basis, sample/result time |
| `evidence_references` | provenance | → finding or event; page, excerpt, region, verification |
| `audit_findings` | findings | unique (`case_id`,`fingerprint`); rule id/source/protocol version, verification and review status, amendments, `superseded` |
| `finding_reviews` | immutable review history | actor from session, previous values |
| `comments` | reviewer comments | polymorphic target (finding/recommendation/review/counterfactual) |
| `preventability_assessments` | preliminary / clinical review / committee decision | framework id+version, score breakdown, inputs, missing information |
| `recommendations` | recommendations & corrective actions | priority, owner, due date, status, `tracked_as_action` |
| `corrective_action_updates` | append-only action status history | |
| `reviews` | collaboration | status, assignee, decision |
| `audit_reports` | versioned reports | unique (`case_id`,`version`); 🔒`content`, `content_sha256` |
| `hospital_protocols` / `protocol_versions` | protocols | unique name; unique (`protocol_id`,`version`); status draft/approved/retired; rules JSON |
| `audit_logs` | security audit trail | append-only (ORM guard + PostgreSQL trigger), HMAC hash chain |
| `assistant_conversations` / `assistant_messages` | assistant history | 🔒message `content` |
| `counterfactual_analyses` | hypothetical analyses | result JSON |

Re-running a job rebuilds derived clinical rows idempotently; findings are matched by fingerprint so human
review state survives reprocessing, and findings no longer produced are marked `superseded`.

Recommended DB hardening: run the app as a role without `TRUNCATE`/`DELETE` on `audit_logs`, enable
PostgreSQL TLS, and use disk/volume encryption for the database and `storage/`.


## Accounts and ownership (migration `0002_accounts`)

| Table / change | Purpose | Key columns / constraints |
|---|---|---|
| `users` (+columns) | sign-in identity | `login_id` unique (Doctor/Admin ID, upper-case), `password_hash` nullable until activation, `status` pending_activation/active/suspended/deactivated/archived (indexed), `status_reason`, `status_changed_at`, `is_activated`, `activated_at` |
| `doctor_profiles` | admin-provisioned doctor record | `user_id` unique FK (RESTRICT), `designation`, `phone`, `contact_info`, `employee_ref` unique, `valid_until`, `created_by`, `password_reset_requested_at`. Name, email and department live on `users` (no duplication) |
| `account_tokens` | activation / reset codes | `purpose`, `token_hash` unique (HMAC), `expires_at`, `used_at`, `revoked_at`, `failed_attempts`, `created_by` |
| `user_preferences` | per-user preferences | PK `user_id`; theme, language, notification/dashboard JSON |
| `auth_settings` | hospital auth policy | single row `id=1` |
| `user_sessions` (+columns) | | `remember`, `revoked_reason` |
| `patients` | doctor-owned patients | `owner_user_id` FK **RESTRICT** (archiving a doctor never deletes patients), unique (`owner_user_id`,`patient_ref`), encrypted `display_name`/`notes`, soft delete |
| `patient_access` | explicit sharing | unique (`patient_id`,`user_id`), `access_level`, `granted_by`, `expires_at`, `revoked_at` |
| `patient_cases.patient_id` | case → patient link | FK SET NULL, indexed |
| `case_assignments` (+columns) | time-limited / audited grants | `expires_at`, `reason`, `granted_by` |
| `audit_logs.target_user_id` | account the event concerns | indexed; included in the hash chain when set |

The data migration maps legacy `disabled`/`locked` statuses to `deactivated` and seeds `auth_settings`.
