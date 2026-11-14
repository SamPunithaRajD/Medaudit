import io
import os
import subprocess
import sys
from pathlib import Path

from app.ai_engine.agents.extraction_agents import VitalsAgent
from app.models import AgentExecution, AuditFinding, ProcessingJob
from app.workers.queue import queue
from tests.conftest import upload_and_process


def test_agent_failure_is_isolated(clients, db, monkeypatch):
    def broken(self, ctx):
        raise ValueError("simulated agent crash")
    monkeypatch.setattr(VitalsAgent, "run", broken)
    from app.utils.sample_casesheet import PAGES, build_sample_pdf  # noqa: F401
    import fitz
    doc = fitz.open(stream=build_sample_pdf(), filetype="pdf")
    doc.load_page(0).insert_text((40, 820), "variant-for-failure-test", fontsize=6)
    body = upload_and_process(clients["doctor"], db, pdf=doc.tobytes())
    job = db.get(ProcessingJob, body["job_id"])
    db.refresh(job)
    assert job.status == "completed"
    assert any("vitals" in w for w in job.warnings)
    ex = db.query(AgentExecution).filter_by(job_id=job.id, agent_name="vitals").one()
    assert ex.status == "failed" and ex.attempts == 2 and "simulated" in ex.error
    other = db.query(AgentExecution).filter_by(job_id=job.id, agent_name="laboratory").one()
    assert other.status == "completed"


def test_reprocess_preserves_reviews_and_skips_done_pages(clients, db):
    import fitz
    from app.utils.sample_casesheet import build_sample_pdf
    doc = fitz.open(stream=build_sample_pdf(), filetype="pdf")
    doc.load_page(0).insert_text((40, 820), "variant-for-reprocess", fontsize=6)
    body = upload_and_process(clients["doctor"], db, pdf=doc.tobytes())
    c = clients["doctor"]
    f = c.get(f"/api/v1/cases/{body['case_id']}/findings").json()["items"][0]
    c.post(f"/api/v1/findings/{f['id']}/review", json={"action": "accept"})
    r = c.post(f"/api/v1/cases/{body['case_id']}/process")
    assert r.status_code == 202
    assert c.post(f"/api/v1/cases/{body['case_id']}/process").status_code == 409  # already active
    from app.workers.worker import run_once
    run_once("test")
    db.expire_all()
    assert db.get(AuditFinding, f["id"]).review_status == "approved"  # human review survives reprocessing


def test_cancel_queued_job(clients, db):
    import fitz
    from app.utils.sample_casesheet import build_sample_pdf
    doc = fitz.open(stream=build_sample_pdf(), filetype="pdf")
    doc.load_page(0).insert_text((40, 820), "variant-for-cancel", fontsize=6)
    r = clients["doctor"].post("/api/v1/cases/upload", files={"file": ("c.pdf", io.BytesIO(doc.tobytes()), "application/pdf")})
    job_id = r.json()["job_id"]
    res = clients["doctor"].post(f"/api/v1/jobs/{job_id}/cancel").json()
    assert res["status"] == "cancelled"
    assert queue.claim(db, "w") != job_id
    assert clients["doctor"].post(f"/api/v1/jobs/{job_id}/cancel").status_code == 409


def test_job_fails_cleanly_on_storage_tamper(clients, db):
    import fitz
    from app.core.config import get_settings
    from app.models import Document
    from app.utils.sample_casesheet import build_sample_pdf
    from app.workers.worker import run_once
    doc = fitz.open(stream=build_sample_pdf(), filetype="pdf")
    doc.load_page(0).insert_text((40, 820), "variant-for-tamper", fontsize=6)
    body = clients["doctor"].post("/api/v1/cases/upload", files={"file": ("t.pdf", doc.tobytes(), "application/pdf")}).json()
    d = db.get(Document, body["document_id"])
    path = get_settings().storage_path / d.storage_key
    path.write_bytes(path.read_bytes()[:-3] + b"xxx")
    run_once("test")
    db.expire_all()
    job = db.get(ProcessingJob, body["job_id"])
    assert job.status == "failed" and job.error_code == "DOCUMENT_INTEGRITY"
    st = clients["doctor"].get(f"/api/v1/cases/{body['case_id']}/status").json()
    assert st["error"]["code"] == "DOCUMENT_INTEGRITY" and st["processing_state"] == "failed"


def test_alembic_migrations_run(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'mig.db'}"}
    root = Path(__file__).resolve().parents[1]
    alembic = [sys.executable, "-m", "alembic"]
    for args in (["upgrade", "head"], ["check"], ["downgrade", "base"], ["upgrade", "head"]):
        r = subprocess.run(alembic + args, cwd=root, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
