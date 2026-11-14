"""Staged multi-agent orchestration.

Stage plan (dependencies flow downward; agents within a stage run sequentially to respect the
memory and single-model limits of a local Ollama runtime):

  stage 0 analyze   : patient_information, clinical_notes, vitals, medication, laboratory, emergency_cpr, nursing
  stage 1 analyze   : documentation           (needs note entries from stage 0)
  stage 2 timeline  : clinical_timeline  → clinical_rule_engine
  stage 3 verify    : evidence_verification → preventability_framework → recommendation_engine
  stage 4 report    : report_generation

Every agent execution is persisted (status, attempts, timing, LLM use, warnings, errors).
Completed agents whose input hash is unchanged are skipped on job retry (partial recovery).
Failures are isolated: a failed agent is recorded and downstream agents proceed with what exists.
"""
from __future__ import annotations

import time
import traceback
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_engine.agents.base import Agent, AgentContext, AgentOutput
from app.ai_engine.agents.extraction_agents import (ClinicalNotesAgent, DocumentationAgent, EmergencyCPRAgent,
                                                    LaboratoryAgent, MedicationAgent, NursingAgent,
                                                    PatientInformationAgent, VitalsAgent)
from app.ai_engine.agents.report_agent import ReportGenerationAgent
from app.ai_engine.agents.verification_agent import EvidenceVerificationAgent
from app.audit_engine.clinical_rules.engine import evaluate_case, persist_findings
from app.audit_engine.preventability.framework import assess
from app.audit_engine.recommendations import engine as rec_engine
from app.audit_engine.timeline.builder import build_timeline
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import AgentExecution
from app.utils.ids import utcnow

log = get_logger(__name__)


class ClinicalTimelineAgent(Agent):
    name = "clinical_timeline"
    description = "Combines structured outputs into a chronological event sequence with conflict flags."

    def run(self, ctx: AgentContext) -> AgentOutput:
        summary = build_timeline(ctx.db, ctx.case, ctx.job.id)
        ctx.shared["timeline_events"] = summary["events"]
        return AgentOutput(summary=summary)


class ClinicalRuleEngineStep(Agent):
    name = "clinical_rule_engine"
    description = "Evaluates approved protocol rules and built-in documentation checks (deterministic)."

    def run(self, ctx: AgentContext) -> AgentOutput:
        cands, meta = evaluate_case(ctx.db, ctx.case)
        res = persist_findings(ctx.db, ctx.case, cands, ctx.job.id)
        return AgentOutput(summary={**res, **meta, "candidates": len(cands)}, warnings=meta["warnings"])


class PreventabilityStep(Agent):
    name = "preventability_framework"
    description = "Computes the transparent preliminary preventability evidence summary."

    def run(self, ctx: AgentContext) -> AgentOutput:
        pa = assess(ctx.db, ctx.case, ctx.job, timeline_events=ctx.shared.get("timeline_events", 0))
        return AgentOutput(summary={"category": pa.category, "score": pa.score, "framework_version": pa.framework_version})


class RecommendationStep(Agent):
    name = "recommendation_engine"
    description = "Creates template-based recommendations for verified findings."

    def run(self, ctx: AgentContext) -> AgentOutput:
        return AgentOutput(summary=rec_engine.generate(ctx.db, ctx.case, ctx.job.id))


STAGES: list[tuple[str, list[type[Agent]]]] = [
    ("analyze", [PatientInformationAgent, ClinicalNotesAgent, VitalsAgent, MedicationAgent, LaboratoryAgent,
                 EmergencyCPRAgent, NursingAgent]),
    ("analyze", [DocumentationAgent]),
    ("timeline", [ClinicalTimelineAgent, ClinicalRuleEngineStep]),
    ("verify_evidence", [EvidenceVerificationAgent, PreventabilityStep, RecommendationStep]),
    ("report", [ReportGenerationAgent]),
]

AGENT_REGISTRY = {cls.name: cls for _, group in STAGES for cls in group}
# Steps that must always re-run because they depend on the whole case state.
ALWAYS_RERUN = {"clinical_timeline", "clinical_rule_engine", "evidence_verification", "preventability_framework",
                "recommendation_engine", "report_generation", "documentation"}


class Orchestrator:
    def __init__(self, db: Session, ctx: AgentContext, on_stage: Callable[[str, str], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None):
        self.db = db
        self.ctx = ctx
        self.on_stage = on_stage or (lambda key, status: None)
        self.should_cancel = should_cancel or (lambda: False)

    def _execution(self, agent: Agent, stage_index: int) -> AgentExecution:
        ex = self.db.scalar(select(AgentExecution).where(AgentExecution.job_id == self.ctx.job.id,
                                                         AgentExecution.agent_name == agent.name))
        if ex is None:
            ex = AgentExecution(job_id=self.ctx.job.id, case_id=self.ctx.case.id, agent_name=agent.name,
                                stage_index=stage_index, status="pending")
            self.db.add(ex)
            self.db.flush()
        return ex

    def run_agent(self, agent: Agent, stage_index: int) -> AgentExecution:
        s = get_settings()
        ex = self._execution(agent, stage_index)
        input_hash = agent.input_hash(self.ctx)
        if ex.status == "completed" and ex.input_hash == input_hash and agent.name not in ALWAYS_RERUN:
            return ex
        for attempt in range(s.agent_max_retries + 1):
            ex.status, ex.attempts, ex.started_at, ex.error = "running", ex.attempts + 1, utcnow(), None
            ex.input_hash = input_hash
            self.db.commit()
            started = time.monotonic()
            try:
                # Status was committed above, so a rollback on failure discards only this agent's writes.
                out = agent.run(self.ctx)
                ex.status, ex.output_summary, ex.warnings = "completed", out.summary, out.warnings[:50]
                ex.llm_used = out.llm_used
                ex.llm_model = self.ctx.llm.model if (out.llm_used and self.ctx.llm) else None
                ex.completed_at = utcnow()
                ex.duration_ms = int((time.monotonic() - started) * 1000)
                self.db.commit()
                return ex
            except Exception as exc:  # isolate agent failure
                self.db.rollback()
                ex = self._execution(agent, stage_index)
                ex.status = "failed"
                ex.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                ex.completed_at = utcnow()
                ex.duration_ms = int((time.monotonic() - started) * 1000)
                self.db.commit()
                log.error("Agent %s failed (attempt %s): %s", agent.name, attempt + 1, type(exc).__name__)
                log.debug(traceback.format_exc())
        return ex

    def run(self, stage_keys: set[str] | None = None) -> dict[str, str]:
        results: dict[str, str] = {}
        for idx, (key, group) in enumerate(STAGES):
            if stage_keys is not None and key not in stage_keys:
                continue
            self.on_stage(key, "running")
            for cls in group:
                if self.should_cancel():
                    raise CancelledError()
                ex = self.run_agent(cls(), idx)
                results[cls.name] = ex.status
            self.on_stage(key, "completed")
        return results


class CancelledError(Exception):
    pass
