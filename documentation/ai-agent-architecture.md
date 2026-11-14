# AI agent architecture

All agents live in `backend/app/ai_engine/agents/`; the controller is `ai_engine/orchestration/orchestrator.py`.

## Stages

| Stage | Agents / steps | Depends on |
|---|---|---|
| analyze (0) | 1 Patient information · 2 Clinical notes · 3 Vitals · 4 Medication · 5 Laboratory · 6 Emergency & CPR · 7 Nursing | classified page text |
| analyze (1) | 8 Documentation | note entries from 2 and 7, page/OCR records |
| timeline | 9 Clinical timeline → clinical rule engine | all structured outputs |
| verify_evidence | 10 Evidence verification → preventability framework → recommendation engine | findings, events |
| report | 11 Report generation | everything above |

Agents in a stage run sequentially (single local model, bounded memory). Each run is persisted in
`agent_executions` with status, attempts, duration, input hash, LLM use, warnings and errors. A failing agent
is retried (`AGENT_MAX_RETRIES`), then recorded as `failed`; its partial writes are rolled back and downstream
agents continue. Job warnings list failed agents and the report provenance shows them. On job retry, agents
whose input hash is unchanged are skipped (except whole-case steps, which always re-run).

## What each agent does

| # | Agent | Deterministic core | Optional LLM (validated) |
|---|---|---|---|
| 1 | patient_information | labelled fields (name, UHID, age, sex, DOA/DOD, dept, ward, cause, diagnoses, complaints, comorbidities), conflict detection, missing-field records; fills case fields unless set by a human | list fields not found by rules |
| 2 | clinical_notes | splits doctor notes into timed/untimed entries | documented clinical decisions with quotes |
| 3 | vitals | HR, BP, SpO2, temp (°F→°C normalised), RR, MAP, GCS, CBG; plausibility checks; incomplete observation sets | — |
| 4 | medication | orders / administrations / changes with dose, route, frequency, time | entries on medication pages rules missed (marked uncertain) |
| 5 | laboratory | results, units, documented reference range, documented flags, sample/result time, repeat tests | — |
| 6 | emergency_cpr | deterioration, ICU request/transfer, senior informed, CPR, procedures, death declaration | — |
| 7 | nursing | nursing entries, entries per shift | — |
| 8 | documentation | missing times, missing signature markers, duplicates, low OCR / illegible pages, failed pages, chronology inconsistencies, possible missing pages (only when "Page n of N" is written) | — |
| 9 | clinical_timeline | projects vitals/labs/meds into events, orders timed events, keeps untimed in source order, flags out-of-admission-window and context-dated events | — |
| 10 | evidence_verification | checks every excerpt against the cited page text (normalised/fuzzy match); sets finding verification; unsupported findings are excluded from scoring/recommendations | — |
| 11 | report_generation | 16-section report, versioned and encrypted; AI and committee sections separate | narrative of clinical course from structured facts, screened |

Medication "discrepancies" are reported only as documentation observations (e.g. order without a documented
administration) and only via an applicable protocol rule — never as medication errors.

## LLM safety controls

- `OllamaClient` refuses non-loopback/non-private endpoints when `LOCAL_ONLY_AI=true`; no external provider code exists.
- Prompts fence document text in `<document>` tags, strip embedded fence tags, cap length and instruct the model to
  treat it as data. The model has no tools, so injected text cannot cause actions.
- Outputs use Ollama JSON-schema `format`, temperature 0; parsed and schema-checked.
- Each item must quote verbatim evidence that is found in the source page, otherwise it is rejected.
- Narrative text is screened: sentences asserting negligence, causation, preventability or prescribing are removed.
- If the model is unavailable or invalid, deterministic logic and templates are used and the job records a warning.

Model: `OLLAMA_MODEL` (default `llama3.2:3b`). Changing the model requires no code changes.
