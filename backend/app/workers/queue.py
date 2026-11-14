"""Replaceable job queue abstraction with a PostgreSQL-backed default implementation.

`DatabaseJobQueue` stores jobs in `processing_jobs` (persistent, survives restarts). On PostgreSQL it
claims with `SELECT ... FOR UPDATE SKIP LOCKED`, so several workers can run safely. Stale jobs whose
worker died (no heartbeat within JOB_LOCK_TIMEOUT_SECONDS) are reclaimed. A Redis/Celery queue can
replace it by implementing the same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import is_postgres
from app.models import ProcessingJob
from app.models.enums import TERMINAL_JOB_STATUSES
from app.utils.ids import utcnow

ACTIVE = ["validating", "ingesting", "preprocessing", "extracting", "classifying", "analyzing", "building_timeline",
          "verifying_evidence", "generating_report"]


class JobQueue(ABC):
    @abstractmethod
    def enqueue(self, db: Session, job: ProcessingJob) -> None: ...

    @abstractmethod
    def claim(self, db: Session, worker_id: str) -> str | None: ...

    @abstractmethod
    def cancel(self, db: Session, job: ProcessingJob) -> bool: ...


class DatabaseJobQueue(JobQueue):
    def enqueue(self, db: Session, job: ProcessingJob) -> None:
        job.status = "queued"
        job.cancel_requested = False
        job.locked_by = job.locked_at = None
        db.flush()

    def _claimable(self):
        stale_before = utcnow() - timedelta(seconds=get_settings().job_lock_timeout_seconds)
        return or_(
            (ProcessingJob.status == "queued") & ProcessingJob.locked_by.is_(None),
            ProcessingJob.status.in_(ACTIVE) & (ProcessingJob.heartbeat_at < stale_before),
        )

    def claim(self, db: Session, worker_id: str) -> str | None:
        now = utcnow()
        q = (select(ProcessingJob.id).where(self._claimable(), ProcessingJob.attempts < ProcessingJob.max_attempts,
                                            ProcessingJob.cancel_requested.is_(False))
             .order_by(ProcessingJob.created_at).limit(1))
        if is_postgres(db):
            job_id = db.scalar(q.with_for_update(skip_locked=True))
            if job_id is None:
                db.rollback()
                return None
            db.execute(update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                locked_by=worker_id, locked_at=now, heartbeat_at=now, attempts=ProcessingJob.attempts + 1))
            db.commit()
            return job_id
        job_id = db.scalar(q)
        if job_id is None:
            return None
        # Optimistic claim for SQLite / single-node development.
        res = db.execute(update(ProcessingJob).where(ProcessingJob.id == job_id, self._claimable()).values(
            locked_by=worker_id, locked_at=now, heartbeat_at=now, attempts=ProcessingJob.attempts + 1))
        db.commit()
        return job_id if res.rowcount == 1 else None

    def cancel(self, db: Session, job: ProcessingJob) -> bool:
        if job.status in TERMINAL_JOB_STATUSES:
            return False
        job.cancel_requested = True
        if job.status == "queued" and job.locked_by is None:
            job.status, job.completed_at = "cancelled", utcnow()
            job.error_code, job.error_message = "CANCELLED", "Cancelled before processing started"
        db.flush()
        return True


queue: JobQueue = DatabaseJobQueue()
