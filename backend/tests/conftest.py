"""Test fixtures. Tests run against a throw-away SQLite DB by default; set TEST_DATABASE_URL to a
PostgreSQL URL to run the same suite against PostgreSQL."""
from __future__ import annotations

import base64
import io
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="medaudit-test-")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP}/test.db"),
    "ENCRYPTION_KEYS": "v1:" + base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "ENCRYPTION_ACTIVE_KEY_VERSION": "v1",
    "SESSION_SECRET": "test-session-secret-" + "x" * 40,
    "AUDIT_LOG_HMAC_KEY": "test-audit-key-" + "y" * 40,
    "STORAGE_DIR": f"{_TMP}/storage",
    "OLLAMA_ENABLED": "false",
    "WORKER_MODE": "disabled",
    "FRONTEND_ORIGINS": "http://localhost:5173",
    "ANALYTICS_MIN_CELL_SIZE": "1",
    "LOGIN_RATE_LIMIT_PER_MINUTE": "1000",
    "SENSITIVE_RATE_LIMIT_PER_MINUTE": "1000",
    "HOSPITAL_TIMEZONE": "UTC",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import reset_settings_cache  # noqa: E402

reset_settings_cache()

from app.core.database import SessionLocal, get_engine  # noqa: E402
from app.models import Base, CaseAssignment, RoleModel, User  # noqa: E402
from app.security.authentication import hash_password  # noqa: E402
from app.security.rate_limit import limiter  # noqa: E402

PASSWORD = "Test-Passw0rd!x"
ROLES = ["doctor", "nurse", "admin", "auditor", "committee_member"]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for r in ROLES:
            db.add(RoleModel(name=r, description=r))
        for r in ROLES:
            db.add(User(email=f"{r}@test.local", display_name=f"Test {r}", role=r, department="Internal Medicine",
                        password_hash=hash_password(PASSWORD)))
        db.add(User(email="outsider@test.local", display_name="Outsider", role="doctor", department="Surgery",
                    password_hash=hash_password(PASSWORD)))
        db.commit()
    yield
    engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()
    yield


@pytest.fixture()
def app():
    from app.main import create_app
    return create_app()


def login(app, role: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"login_id": f"{role}@test.local", "password": PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]  # browsers send this header for state changes
    return c


@pytest.fixture()
def clients(app):
    with TestClient(app):  # run lifespan once
        pass
    return {r: login(app, r) for r in ROLES + ["outsider"]}


def user_id(db, role: str) -> str:
    return db.query(User).filter(User.email == f"{role}@test.local").one().id


def upload_and_process(client: TestClient, db, *, pdf: bytes | None = None, assign: tuple[str, ...] = ROLES) -> dict:
    from app.utils.sample_casesheet import build_sample_pdf
    from app.workers.worker import run_once
    pdf = pdf or build_sample_pdf(include_duplicate=True)
    r = client.post("/api/v1/cases/upload", files={"file": ("case.pdf", io.BytesIO(pdf), "application/pdf")},
                    data={"department": "Internal Medicine", "patient_ref": "SYN-T"})
    assert r.status_code == 202, r.text
    body = r.json()
    for role in assign:
        uid = user_id(db, role)
        if not db.query(CaseAssignment).filter_by(case_id=body["case_id"], user_id=uid).first():
            db.add(CaseAssignment(case_id=body["case_id"], user_id=uid, assignment_role="reviewer"))
    db.commit()
    while run_once("test"):
        pass
    return body


@pytest.fixture()
def demo_protocol(db):
    from datetime import date

    from scripts.seed_demo import DEMO_PROTOCOL_RULES
    from app.models import HospitalProtocol, ProtocolVersion
    from app.utils.ids import utcnow
    p = db.query(HospitalProtocol).filter_by(name="TEST protocol").first()
    if p is None:
        p = HospitalProtocol(name="TEST protocol", category="monitoring")
        db.add(p)
        db.flush()
        db.add(ProtocolVersion(protocol_id=p.id, version="1.0", status="approved", effective_date=date(2026, 1, 1),
                               rules=DEMO_PROTOCOL_RULES, approved_at=utcnow()))
        db.commit()
    yield p
    db.execute(text("DELETE FROM protocol_versions WHERE protocol_id = :p"), {"p": p.id})
    db.execute(text("DELETE FROM hospital_protocols WHERE id = :p"), {"p": p.id})
    db.commit()
