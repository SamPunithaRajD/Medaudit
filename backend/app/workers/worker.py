"""Background worker loop. Run standalone (`python -m app.workers.run_worker`) or embedded in the API
process for development (WORKER_MODE=embedded)."""
from __future__ import annotations

import os
import socket
import threading
import time
import uuid

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.processing_service import execute_job
from app.workers.queue import queue

log = get_logger(__name__)


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def run_once(worker_id: str | None = None) -> str | None:
    """Claim and execute a single job. Returns the job id or None if the queue is empty."""
    worker_id = worker_id or worker_identity()
    with SessionLocal() as db:
        job_id = queue.claim(db, worker_id)
    if job_id is None:
        return None
    log.info("Worker %s processing job %s", worker_id, job_id)
    with SessionLocal() as db:
        execute_job(db, job_id, worker_id)
    return job_id


def run_forever(stop: threading.Event | None = None) -> None:
    s = get_settings()
    worker_id = worker_identity()
    stop = stop or threading.Event()
    log.info("Worker %s started (poll every %ss)", worker_id, s.worker_poll_interval)
    while not stop.is_set():
        try:
            if run_once(worker_id) is None:
                stop.wait(s.worker_poll_interval)
        except Exception as exc:  # keep the worker alive
            log.error("Worker loop error: %s", type(exc).__name__)
            time.sleep(min(s.worker_poll_interval * 2, 10))


class EmbeddedWorker:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(max(1, get_settings().worker_concurrency)):
            t = threading.Thread(target=run_forever, args=(self.stop,), name=f"medaudit-worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)

    def shutdown(self) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(timeout=5)
