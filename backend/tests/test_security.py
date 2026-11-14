import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models import AuditLog, User
from tests.conftest import PASSWORD, login, upload_and_process


@pytest.fixture()
def case(clients, db):
    return upload_and_process(clients["doctor"], db, assign=("doctor", "nurse"))


def test_unauthenticated_rejected(app):
    c = TestClient(app)
    for path in ("/api/v1/cases", "/api/v1/auth/me", "/api/v1/analytics/overview", "/api/v1/system/status"):
        r = c.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    c.cookies.set("medaudit_session", "forged-token")
    assert c.get("/api/v1/auth/me").status_code == 401


def test_login_logout_and_session_revocation(app):
    c = login(app, "auditor")
    assert c.get("/api/v1/auth/me").json()["user"]["role"] == "auditor"
    cookie = c.cookies.get("medaudit_session")
    assert c.post("/api/v1/auth/logout").status_code == 200
    replay = TestClient(app)
    replay.cookies.set("medaudit_session", cookie)
    assert replay.get("/api/v1/auth/me").status_code == 401


def test_cookie_flags_and_token_not_in_browser_body(app):
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": "doctor@test.local", "password": PASSWORD})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc
    assert "token" not in r.json()["session"]
    n = TestClient(app).post("/api/v1/auth/login", json={"email": "doctor@test.local", "password": PASSWORD},
                             headers={"X-MedAudit-Client": "native"}).json()
    bearer = TestClient(app)
    assert bearer.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {n['session']['token']}"}).status_code == 200


def test_bad_password_lockout_and_generic_error(app, db):
    c = TestClient(app)
    for _ in range(5):
        r = c.post("/api/v1/auth/login", json={"email": "nurse@test.local", "password": "wrong"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = c.post("/api/v1/auth/login", json={"email": "nurse@test.local", "password": PASSWORD})
    assert r.status_code == 401  # locked out
    unknown = c.post("/api/v1/auth/login", json={"email": "nobody@test.local", "password": "x"}).json()
    assert unknown["error"]["code"] == "INVALID_CREDENTIALS"  # no account enumeration
    u = db.query(User).filter_by(email="nurse@test.local").one()
    u.locked_until = None
    db.commit()


def test_disabled_user_sessions_end(app, clients, db):
    c = login(app, "auditor")
    uid = db.query(User).filter_by(email="auditor@test.local").one().id
    assert clients["admin"].patch(f"/api/v1/admin/users/{uid}", json={"status": "disabled"}).status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401
    clients["admin"].patch(f"/api/v1/admin/users/{uid}", json={"status": "active"})


def test_cross_case_access_is_404(clients, case):
    out = clients["outsider"]
    cid = case["case_id"]
    for path in (f"/api/v1/cases/{cid}", f"/api/v1/cases/{cid}/timeline", f"/api/v1/cases/{cid}/findings",
                 f"/api/v1/cases/{cid}/documents", f"/api/v1/cases/{cid}/audit-report", f"/api/v1/jobs/{case['job_id']}",
                 f"/api/v1/documents/{case['document_id']}/pages/1/text",
                 f"/api/v1/documents/{case['document_id']}/download"):
        r = out.get(path)
        assert r.status_code == 404, path
    assert out.post("/api/v1/assistant/message", json={"case_id": cid, "message": "summary"}).status_code == 404
    assert cid not in str(out.get("/api/v1/cases").json())
    # Administrators do not get automatic access to clinical records...
    admin = clients["admin"]
    assert admin.get(f"/api/v1/cases/{cid}").status_code == 404
    assert cid not in str(admin.get("/api/v1/cases").json())
    # ...only through the explicit, justified, audited emergency procedure.
    assert admin.post(f"/api/v1/cases/{cid}/emergency-access", json={"reason": "short"}).status_code == 422
    r = admin.post(f"/api/v1/cases/{cid}/emergency-access",
                   json={"reason": "Coroner request for urgent record review, ref 2026-77", "duration_minutes": 30})
    assert r.status_code == 201 and r.json()["duration_minutes"] == 30
    assert admin.get(f"/api/v1/cases/{cid}").status_code == 200
    # Doctors cannot use the emergency procedure.
    assert out.post(f"/api/v1/cases/{cid}/emergency-access",
                    json={"reason": "I would like to look at this record please"}).status_code == 403


def test_role_restrictions(clients, case):
    nurse = clients["nurse"]
    assert nurse.get(f"/api/v1/documents/{case['document_id']}/download").status_code == 403
    assert nurse.get("/api/v1/analytics/overview").status_code == 403
    assert nurse.get("/api/v1/admin/users").status_code == 403
    assert clients["doctor"].patch(f"/api/v1/cases/{case['case_id']}", json={"review_status": "approved"}).status_code == 403


def test_invalid_uploads(clients):
    c = clients["doctor"]
    r = c.post("/api/v1/cases/upload", files={"file": ("x.exe", b"MZ....", "application/pdf")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    r = c.post("/api/v1/cases/upload", files={"file": ("x.pdf", b"PK\x03\x04", "application/pdf")})
    assert r.status_code == 415
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../escape.png", b"\x89PNG\r\n\x1a\n")
    r = c.post("/api/v1/cases/upload", files={"file": ("x.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ARCHIVE"
    r = c.post("/api/v1/cases/upload", files={"file": ("x.pdf", b"%PDF-1.4 broken", "application/pdf")})
    assert r.status_code == 422
    r = c.post("/api/v1/cases/upload", files={"file": ("x.pdf", b"%PDF" + b"0" * 10, "application/pdf")},
               headers={"content-length": str(500 * 1024 * 1024)})
    assert r.status_code in (413, 422)


def test_upload_too_large(clients, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.get_settings(), "max_upload_size_mb", 0)
    r = clients["doctor"].post("/api/v1/cases/upload", files={"file": ("x.pdf", b"%PDF-1.4" + b"0" * 100, "application/pdf")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_duplicate_upload(clients, db):
    from app.utils.sample_casesheet import build_sample_pdf
    pdf = build_sample_pdf()
    first = clients["doctor"].post("/api/v1/cases/upload", files={"file": ("a.pdf", pdf, "application/pdf")})
    assert first.status_code == 202
    r = clients["doctor"].post("/api/v1/cases/upload", files={"file": ("again.pdf", pdf, "application/pdf")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_UPLOAD"
    # A user without access to the first case is not told it exists.
    other = clients["outsider"].post("/api/v1/cases/upload", files={"file": ("b.pdf", pdf, "application/pdf")})
    assert other.status_code == 202


def test_cors_allowlist_and_csrf(app, clients):
    c = TestClient(app)
    ok = c.options("/api/v1/cases", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert ok.headers.get("access-control-allow-credentials") == "true"
    bad = c.options("/api/v1/cases", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    r = clients["doctor"].post("/api/v1/auth/logout", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_REJECTED"


def test_security_headers_and_error_envelope(clients):
    r = clients["doctor"].get("/api/v1/cases/DOES-NOT-EXIST")
    body = r.json()
    assert r.status_code == 404 and set(body["error"]) == {"code", "message", "details", "request_id"}
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-request-id"] == body["error"]["request_id"]


def test_no_sensitive_leakage_in_errors(app):
    from app.main import create_app
    a = create_app()

    @a.get("/boom")
    def boom():
        raise RuntimeError("SECRET-PATIENT-DATA")
    r = TestClient(a, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500 and "SECRET" not in r.text and r.json()["error"]["code"] == "INTERNAL_ERROR"


def test_document_encrypted_at_rest(clients, db, case):
    from app.core.config import get_settings
    from app.models import Document, OCRResult
    doc = db.get(Document, case["document_id"])
    raw = (get_settings().storage_path / doc.storage_key).read_bytes()
    assert raw.startswith(b"MAE1") and b"%PDF" not in raw[:100] and b"Test Patient Alpha" not in raw
    stored = db.execute(text("SELECT text FROM ocr_results LIMIT 1")).scalar()
    assert "SYNTHETIC" not in stored
    # Tampering with the stored file is detected on access.
    p = get_settings().storage_path / doc.storage_key
    tampered = bytearray(raw)
    tampered[-5] ^= 0xFF
    p.write_bytes(bytes(tampered))
    r = clients["doctor"].get(f"/api/v1/documents/{doc.id}/download")
    assert r.status_code == 503 and r.json()["error"]["code"] == "DOCUMENT_INTEGRITY"
    p.write_bytes(raw)
    assert db.query(OCRResult).count() > 0


def test_audit_log_events_chain_and_append_only(clients, db, case):
    actions = {a for (a,) in db.query(AuditLog.action).all()}
    assert {"auth.login", "case.upload"} <= actions
    clients["doctor"].get(f"/api/v1/documents/{case['document_id']}/download")
    assert db.query(AuditLog).filter_by(action="document.download", case_id=case["case_id"]).count() >= 1
    assert clients["admin"].get("/api/v1/admin/audit-logs/verify").json()["valid"] is True
    # ORM refuses updates.
    row = db.query(AuditLog).first()
    row.action = "tampered"
    with pytest.raises(RuntimeError):
        db.flush()
    db.rollback()
    # Out-of-band tampering breaks the hash chain.
    seq = db.query(AuditLog.seq).order_by(AuditLog.seq).first()[0]
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE audit_logs SET outcome='denied' WHERE seq=:s"), {"s": seq})
        db.commit()
        res = clients["admin"].get("/api/v1/admin/audit-logs/verify").json()
        assert res["valid"] is False
        db.execute(text("UPDATE audit_logs SET outcome='success' WHERE seq=:s"), {"s": seq})
        db.commit()
    details = " ".join(str(d) for (d,) in db.query(AuditLog.details).all())
    assert "Test Patient Alpha" not in details and PASSWORD not in details
