# MedAudit AI 2.0 · Frontend ↔ Backend contract (proposed)

Version 1.0 · 13 November 2026. Proposed contract for the independent FastAPI service, not a claim these endpoints exist. Base path /api/v1. IDs are opaque strings. Timestamps are RFC 3339 UTC. The backend owns authentication, authorization, audit trails, retention, encryption and configured clinical rules. UI role selection is demonstrative only.

## Shared conventions

JSON request/response bodies use application/json; upload uses multipart/form-data. Lists return {"items":[],"pagination":{"page":1,"page_size":25,"total_items":0,"total_pages":0,"next_page":null,"previous_page":null}}. Defaults: page=1, page_size=25; max page_size=100. Common filters: department, status, from, to, q. Error shape: {"error":{"code":"VALIDATION_ERROR","message":"Human-readable message","details":[{"field":"file","issue":"Unsupported file type"}],"request_id":"req_opaque"}}. HTTP codes: 400 malformed, 401 unauthenticated, 403 unauthorized, 404 missing/inaccessible, 409 conflict, 413 too large, 415 unsupported type, 422 validation, 429 throttled, 500 internal, 503 unavailable. Never log patient data, content, tokens or secrets.

Enumerations:

- CaseStatus: processing, pending_review, under_review, committee_review, approved, corrective_action_tracking, closed, archived, failed.
- ReviewStatus: pending, under_review, committee_review, approved, rejected, amended.
- FindingCategory: medical_management, nursing_monitoring, documentation_quality, system_process.
- Severity: low, moderate, high, critical (descriptive; local policy determines interpretation).
- PreventabilityCategory: preventable, possibly_preventable, unavoidable, undetermined. Committee owned; AI output is preliminary.
- RecommendationStatus: open, in_progress, complete, reopened, cancelled.
- DeathLocation: in_hospital, in_transit, brought_dead, unknown.
- JobStatus: queued, validating, ingesting, preprocessing, extracting, classifying, analyzing, building_timeline, verifying_evidence, generating_report, completed, failed, cancelled.
- Role: doctor, nurse, admin, auditor, committee_member (server authorization required).

## Common entity schemas

Case: {id:string, patient_ref:string, patient:{display_name:string|null,age_years:number|null,gender:string|null}, department:string|null, ward:string|null, admitted_at:datetime|null, outcome_at:datetime|null, cause_as_recorded:string|null, death_location:DeathLocation, status:CaseStatus, review_status:ReviewStatus, assigned_reviewers:[{user_id:string,display_name:string,role:Role}], created_at:datetime, updated_at:datetime}.

EvidenceReference: {document_id:string, page_number:integer, region:{x:number,y:number,width:number,height:number,coordinate_space:"normalized_0_1"}|null, excerpt:string|null, event_at:datetime|null, verification_status:"unverified"|"verified"|"disputed", extraction_confidence:number|null, confidence_method:"calibrated"|"heuristic"|"human"|"unavailable"}. Confidence must be null if no meaningful method exists; do not present arbitrary numeric certainty.

Finding: {id:string,case_id:string,category:FindingCategory,title:string,description:string,severity:Severity,evidence:[EvidenceReference],uncertainty:[string],requires_human_verification:boolean,review_status:ReviewStatus,reviewer_comments:[object],created_at:datetime}.

Recommendation: {id:string,case_id:string,finding_id:string|null,category:"treatment"|"nursing"|"documentation"|"system_process"|"equipment"|"staff_training",description:string,priority:Severity,suggested_owner:string|null,due_date:date|null,status:RecommendationStatus,reviewer_comments:[object],created_at:datetime,updated_at:datetime}.

## Endpoints

### POST /api/v1/cases/upload

Multipart fields: file (required, one PDF or ZIP); optional department, patient_ref, metadata JSON object. Optional Idempotency-Key header. Validate signature/MIME, archive contents, malware policy, access, size and page limits. Recommended configurable max is 100 MiB. Stream to quarantine/object storage; do not trust filename or client MIME. Return 202: {case_id:string,document_id:string,job_id:string,status:JobStatus,created_at:datetime,status_url:string,estimated_seconds:number|null}. Errors: 413 FILE_TOO_LARGE, 415 UNSUPPORTED_MEDIA_TYPE, 422 INVALID_ARCHIVE, 409 DUPLICATE_UPLOAD.

### GET /api/v1/cases

Query page, page_size, sort (default created_at:desc), status, department, from, to, q. Returns paginated list of Case objects.

### GET /api/v1/cases/{case_id}

Returns {case:Case,document_count:integer,finding_count:integer,recommendation_count:integer}. Return 404 CASE_NOT_FOUND if absent or inaccessible to avoid existence leaks.

### GET /api/v1/cases/{case_id}/status

Returns {case_id:string,job_id:string,status:JobStatus,progress_percent:number|null,stage:string|null,stages:[{key:string,label:string,status:"queued"|"running"|"completed"|"failed",started_at:datetime|null,completed_at:datetime|null}],updated_at:datetime,error:{code:string,message:string,retryable:boolean}|null}. Proposed stage keys: ingest, preprocess, ocr, classify, extract_entities, analyze, timeline, verify_evidence, report. Report only truthful persisted states. Completed job does not imply approved audit. Poll with bounded backoff; stop on terminal states.

### GET /api/v1/cases/{case_id}/documents

Paginated. Items: {id:string,case_id:string,filename:string,media_type:string,size_bytes:integer,page_count:integer|null,status:"uploaded"|"processing"|"ready"|"failed",uploaded_at:datetime,preview_url:string|null}. Preview URL, if supplied, must be short lived and access-controlled. Return page previews individually; never transmit hundreds of pages at once.

### GET /api/v1/cases/{case_id}/audit-report

Returns {case:Case,summary:{overview:string,clinical_course:string,major_findings:[string],treatment_gaps:[string],documentation_observations:[string],recommendations:[string]},assessment:{category:PreventabilityCategory,rationale:string,preliminary:boolean,framework_id:string|null,framework_version:string|null,confidence:number|null,uncertainty:[string],committee_status:ReviewStatus},findings:[Finding],timeline:[ClinicalEvent],recommendations:[Recommendation],review_status:ReviewStatus,generated_at:datetime}. Generated content includes provenance and provisional status. Missing fields are null, never fabricated.

### GET /api/v1/cases/{case_id}/timeline

Query pagination, type, from, to, q; sort defaults occurred_at:asc. Returns paginated ClinicalEvent: {id:string,case_id:string,occurred_at:datetime|null,occurred_at_precision:"second"|"minute"|"hour"|"date"|"unknown",type:"doctor_note"|"nursing_note"|"vital"|"laboratory"|"medication"|"emergency"|"cpr"|"icu_escalation"|"procedure"|"other",title:string,description:string,severity:Severity|null,evidence:[EvidenceReference],source_status:"extracted"|"human_verified"|"uncertain"}. Never imply exact timestamps from uncertain source data.

### GET /api/v1/cases/{case_id}/findings

Query pagination, category, severity, review_status, sort. Returns paginated Finding list.

### POST /api/v1/findings/{finding_id}/review

Body: {action:"accept"|"reject"|"amend"|"comment",review_status:ReviewStatus,comment:string|null,amended_title:string|null,amended_description:string|null,expected_updated_at:datetime|null}. Actor is sourced from authenticated server session, never trusted from request body. Return {finding:Finding,review:{id:string,actor:{user_id:string,display_name:string},action:string,comment:string|null,created_at:datetime},case_review_status:ReviewStatus}. Persist immutable audit history. 409 REVIEW_CONFLICT on stale changes.

### GET /api/v1/cases/{case_id}/recommendations

Query pagination, status, category, owner_id, sort. Returns paginated Recommendation list.

### POST /api/v1/actions

Body: {case_id:string,finding_id:string|null,category:string,description:string,priority:Severity,suggested_owner:string|null,owner_id:string|null,due_date:date|null}. Return 201 {recommendation:Recommendation}.

### PATCH /api/v1/actions/{action_id}

Partial body: status:RecommendationStatus, owner_id:string, due_date:date|null, reviewer_comment:string, expected_updated_at:datetime. Return {recommendation:Recommendation}. Audit status changes.

### GET /api/v1/analytics/overview

Query from, to, department, review_status, finding_category. Aggregate only; suppress small cohorts and risk of re-identification. Response: {period:{from:date,to:date},filters:object,total_deaths_reviewed:integer,preventability_distribution:{preventable:integer|null,possibly_preventable:integer|null,unavoidable:integer|null,undetermined:integer|null},average_review_days:number|null,recurring_findings:[{category:FindingCategory,count:integer}],department_comparison:[{department:string,case_count:integer}],monthly_trends:[{month:string,reviewed_count:integer}],corrective_action_completion_rate:number|null,suppression_applied:boolean}. Null means unavailable or suppressed, not zero. No patient identifiers.

### POST /api/v1/assistant/message

Body: {case_id:string,message:string,conversation_id:string|null}. Response: {conversation_id:string,message:{role:"assistant",content:string,created_at:datetime,provisional:boolean,evidence:[EvidenceReference],limitations:[string],requires_human_verification:boolean}}. Verify case authorization, constrain context, cite exact document/page. No unsupported medical advice, causation claims or autonomous preventability conclusions.

## Processing, clinical data and operational requirements

Upload creates case, document and asynchronous job. Job progresses queued → validating → ingesting → preprocessing → extracting → classifying → analyzing → building_timeline → verifying_evidence → generating_report → completed; applicable failures may end failed or cancelled. Backend may parallelize internals but reports truthful persisted states. Case review status progresses independently: pending → under_review → committee_review → approved → corrective_action_tracking → closed. Committee approval requires authorized human action and audit log.

VitalObservation: {id,case_id,observed_at:datetime|null,precision:string,measurement:{type:string,value:number|null,unit:string|null},evidence:[EvidenceReference],source_status:string}. Medication and lab entities carry typed values, units, time precision, source references and uncertainty. Never plot incompatible units on a shared unexplained axis.

For production ZIP inspection is server-side; client previews in mock mode are synthetic. Page previews should be individually requested, resized/streamed, access controlled and expire predictably. Authorization applies to every case/document/page request. Retention, encryption, malware scanning and consent policy are backend responsibilities. Never put API secrets in VITE_* variables, local storage, logs or client bundles.

VITE_API_BASE_URL selects API origin; VITE_USE_MOCK_DATA=true selects bundled synthetic fixtures and simulated UI workflows. Mock mode must not claim OCR or model analysis occurred. Backend mode handles 401 sign-in, 403 denied, 404 unavailable, 409 refresh/reconcile, upload 413/415/422, 429 Retry-After and network/5xx retry while preserving draft work. Avoid logging request bodies. Use bounded polling with backoff.


## Accounts and authentication (added 14 November 2026)

Implemented by the backend and consumed by `src/portal/*`. Full endpoint list:
`backend-api-contract.md` §6; design and policies: `authentication-architecture.md`.

- Sign in with `POST /api/v1/auth/login {login_id, password, remember_me}`. The server sets an HttpOnly session
  cookie and returns `{user, session, csrf_token, redirect_to}`. Send `X-CSRF-Token: <csrf_token>` on every
  cookie-authenticated POST/PUT/PATCH/DELETE. `GET /api/v1/auth/me` restores `{user, csrf_token, preferences}` after reload.
- Role (`admin`, `doctor`, …) and permissions come only from the server. UI role selection is not used.
- Doctor IDs are provisioned by administrators. First use goes through `POST /api/v1/auth/doctor/activate`.
- Doctors use `/api/v1/doctor/*` (scoped to their own and shared records). Administrators use `/api/v1/admin/*`
  and do not see clinical records unless explicitly assigned or via audited emergency access.
- New error codes: `INVALID_CREDENTIALS`, `ACCOUNT_SUSPENDED`, `ACCOUNT_INACTIVE`, `ACTIVATION_INVALID`,
  `RESET_INVALID`, `CSRF_REJECTED`, `DOCTOR_CONFLICT`, `PATIENT_EXISTS`, `PATIENT_NOT_FOUND`, `INVALID_STATE`.
