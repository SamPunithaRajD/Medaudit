from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.types import JSONType, UTCDateTime
from app.utils.ids import new_id


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("JOB"))
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    job_type: Mapped[str] = mapped_column(String(30), default="full_pipeline")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    completed_pages: Mapped[int] = mapped_column(Integer, default=0)
    failed_pages: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)

    stages: Mapped[list["JobStage"]] = relationship(back_populates="job", cascade="all, delete-orphan",
                                                    order_by="JobStage.order")


class JobStage(Base):
    __tablename__ = "job_stages"
    __table_args__ = (UniqueConstraint("job_id", "key", name="job_stage_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("STG"))
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(100))
    order: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)

    job: Mapped[ProcessingJob] = relationship(back_populates="stages")


class AgentExecution(Base, TimestampMixin):
    __tablename__ = "agent_executions"
    __table_args__ = (UniqueConstraint("job_id", "agent_name", name="job_agent"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AGX"))
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("patient_cases.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(60))
    stage_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
