"""Doctor provisioning, activation, login, sessions, admin portal and doctor workspace isolation."""
import io
import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models import AccountToken, AuditLog, User
from app.utils.ids import utcnow
from tests.conftest import login

NEW_PW = "Ward-Round-2026!q"


def uniq(prefix="DOC"):
    return f"{prefix}{secrets.randbelow(900000) + 100000}"


def provision(admin: TestClient, **over) -> dict:
    did = over.pop("doctor_id", uniq())
    body = {"doctor_id": did, "full_name": "Dr. Test Example", "email": f"{(did or uniq('auto')).lower()}@hospital.test",
            "department": "General Medicine", "designation": "Senior Consultant", **over}
    r = admin.post("/api/v1/admin/doctors", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def activate(app, doctor: dict, code: str, password: str = NEW_PW, email: str | None = None):
    c = TestClient(app)
    r = c.post("/api/v1/auth/doctor/activate", json={
        "doctor_id": doctor["doctor_id"], "email": email or doctor["email"], "activation_code": code,
        "password": password, "confirm_password": password})
    if r.status_code == 201:
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c, r


def doctor_client(app, admin) -> tuple[TestClient, dict]:
    d = provision(admin)
    c, r = activate(app, d["doctor"], d["activation"]["activation_code"])
    assert r.status_code == 201, r.text
    return c, d["doctor"]


# --- provisioning & activation ---------------------------------------------------------

def test_admin_provisions_doctor_pending_activation(clients, db):
    admin = clients["admin"]
    d = provision(admin)
    doc = d["doctor"]
    assert doc["account_status"] == "pending_activation" and doc["is_activated"] is False
    assert doc["activation_status"] == "pending"
    code = d["activation"]["activation_code"]
    assert len(code) == 14 and d["activation"]["activation_url"].endswith(f"doctor_id={doc['doctor_id']}")
    # Code is stored only as a keyed hash; no password is set.
    u = db.query(User).filter_by(login_id=doc["doctor_id"]).one()
    assert u.password_hash is None
    tok = db.query(AccountToken).filter_by(user_id=u.id).one()
    assert code.replace("-", "") not in tok.token_hash and len(tok.token_hash) == 64
    # Auto-generated IDs and duplicate detection.
    auto = provision(admin, doctor_id=None, email="auto.gen@hospital.test")
    assert auto["doctor"]["doctor_id"].startswith("DOC")
    dup = admin.post("/api/v1/admin/doctors", json={"doctor_id": doc["doctor_id"], "full_name": "X Y",
                                                     "email": doc["email"], "department": "Surgery"})
    assert dup.status_code == 409 and {x["field"] for x in dup.json()["error"]["details"]} == {"doctor_id", "email"}


def test_activation_happy_path_and_single_use(app, clients, db):
    d = provision(clients["admin"])
    doc, code = d["doctor"], d["activation"]["activation_code"]
    c, r = activate(app, doc, code.lower().replace("-", ""))  # case/format-insensitive
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["redirect_to"] == "/doctor" and body["user"]["role"] == "doctor"
    assert c.get("/api/v1/doctor/dashboard").json()["doctor"]["doctor_id"] == doc["doctor_id"]
    u = db.query(User).filter_by(login_id=doc["doctor_id"]).one()
    db.refresh(u)
    assert u.password_hash.startswith("$argon2id$") and NEW_PW not in u.password_hash
    # Re-activation of an activated ID is rejected, also with the same code.
    _, again = activate(app, doc, code)
    assert again.status_code == 422 and again.json()["error"]["code"] == "ACTIVATION_INVALID"
    assert db.query(AuditLog).filter_by(action="auth.activation", target_user_id=u.id, outcome="success").count() == 1


@pytest.mark.parametrize("case", ["unknown_id", "wrong_email", "wrong_code", "expired", "suspended", "weak_password"])
def test_activation_rejections(app, clients, db, case):
    admin = clients["admin"]
    d = provision(admin)
    doc, code = dict(d["doctor"]), d["activation"]["activation_code"]
    email = None
    if case == "unknown_id":
        doc["doctor_id"] = "NOPE" + uniq("")
    elif case == "wrong_email":
        email = "someone.else@hospital.test"
    elif case == "wrong_code":
        code = "AAAA-BBBB-CCCC"
    elif case == "expired":
        db.execute(text("UPDATE account_tokens SET expires_at = :t WHERE user_id = :u"),
                   {"t": (utcnow() - timedelta(hours=1)).replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite"
                    else utcnow() - timedelta(hours=1), "u": doc["user_id"]})
        db.commit()
    elif case == "suspended":
        assert admin.post(f"/api/v1/admin/doctors/{doc['doctor_id']}/suspend", json={"reason": "Credential check"}).status_code == 200
    _, r = activate(app, doc, code, password="weak" if case == "weak_password" else NEW_PW, email=email)
    assert r.status_code == 422
    expected = "VALIDATION_ERROR" if case == "weak_password" else "ACTIVATION_INVALID"
    assert r.json()["error"]["code"] == expected
    if case != "weak_password":
        # Same message for every failure → no account enumeration.
        assert "invalid or have expired" in r.json()["error"]["message"]


def test_activation_code_burns_after_attempts(app, clients):
    d = provision(clients["admin"])
    for _ in range(5):
        activate(app, d["doctor"], "WRON-GCOD-EXXX")
    from app.security.rate_limit import limiter
    limiter.reset()
    _, r = activate(app, d["doctor"], d["activation"]["activation_code"])
    assert r.status_code == 422  # token revoked after 5 failures; admin must resend
    re = clients["admin"].post(f"/api/v1/admin/doctors/{d['doctor']['doctor_id']}/resend-activation").json()
    _, ok = activate(app, d["doctor"], re["activation"]["activation_code"])
    assert ok.status_code == 201


def test_cannot_choose_role_or_invent_id(app):
    c = TestClient(app)
    r = c.post("/api/v1/auth/doctor/activate", json={"doctor_id": "DOC000001", "email": "a@b.test",
                                                     "activation_code": "AAAA-AAAA-AAAA", "password": NEW_PW,
                                                     "confirm_password": NEW_PW, "role": "admin"})
    assert r.status_code == 422  # unknown fields are rejected outright
    assert c.post("/api/v1/admin/users", json={}).status_code == 401  # no public admin registration


# --- login & sessions ---------------------------------------------------------------------

def test_doctor_login_by_id_and_role_redirects(app, clients):
    c, doc = doctor_client(app, clients["admin"])
    fresh = TestClient(app)
    r = fresh.post("/api/v1/auth/login", json={"login_id": doc["doctor_id"].lower(), "password": NEW_PW})
    assert r.status_code == 200 and r.json()["redirect_to"] == "/doctor"
    a = TestClient(app).post("/api/v1/auth/login", json={"login_id": "admin@test.local", "password": "Test-Passw0rd!x"})
    assert a.json()["redirect_to"] == "/admin" and a.json()["user"]["role"] == "admin"
    bad = TestClient(app).post("/api/v1/auth/login", json={"login_id": doc["doctor_id"], "password": "Wrong-pass-123!"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    # Pending (never activated) accounts get the same generic error as unknown IDs.
    pend = provision(clients["admin"])["doctor"]
    p = TestClient(app).post("/api/v1/auth/login", json={"login_id": pend["doctor_id"], "password": NEW_PW})
    unk = TestClient(app).post("/api/v1/auth/login", json={"login_id": "DOC-NOT-REAL", "password": NEW_PW})
    assert p.json()["error"] == {**unk.json()["error"], "request_id": p.json()["error"]["request_id"]}


def test_csrf_required_for_cookie_writes(app, clients):
    c, _ = doctor_client(app, clients["admin"])
    token = c.headers.pop("X-CSRF-Token")
    r = c.patch("/api/v1/doctor/preferences", json={"theme": "dark"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_REJECTED"
    assert c.patch("/api/v1/doctor/preferences", json={"theme": "dark"}, headers={"X-CSRF-Token": "x" * 48}).status_code == 403
    ok = c.patch("/api/v1/doctor/preferences", json={"theme": "dark"}, headers={"X-CSRF-Token": token})
    assert ok.status_code == 200 and ok.json()["preferences"]["theme"] == "dark"
    assert c.get("/api/v1/auth/me").json()["csrf_token"] == token


def test_sessions_list_revoke_and_remember_me(app, clients):
    c, doc = doctor_client(app, clients["admin"])
    other = TestClient(app)
    r = other.post("/api/v1/auth/login", json={"login_id": doc["doctor_id"], "password": NEW_PW, "remember_me": True})
    assert "max-age" in r.headers["set-cookie"].lower() and r.json()["session"]["remember"] is True
    sessions = c.get("/api/v1/auth/sessions").json()["items"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    assert c.post("/api/v1/auth/sessions/revoke-others").json()["revoked"] == 1
    assert other.get("/api/v1/auth/me").status_code == 401  # revoked session cannot be reused
    assert c.get("/api/v1/auth/me").status_code == 200
    me_sid = next(s["id"] for s in c.get("/api/v1/auth/sessions").json()["items"] if s["current"])
    assert c.delete(f"/api/v1/auth/sessions/{me_sid}").json()["current"] is True
    assert c.get("/api/v1/auth/me").status_code == 401
    activity = login(app, "admin")  # sanity: other users unaffected
    assert activity.get("/api/v1/auth/me").status_code == 200


def test_session_expiry(app, clients, db):
    c, doc = doctor_client(app, clients["admin"])
    u = db.query(User).filter_by(login_id=doc["doctor_id"]).one()
    past = utcnow() - timedelta(minutes=1)
    db.execute(text("UPDATE user_sessions SET expires_at = :t WHERE user_id = :u"),
               {"t": past.replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite" else past, "u": u.id})
    db.commit()
    assert c.get("/api/v1/auth/me").status_code == 401


def test_password_change_and_reset_flow(app, clients, db):
    admin = clients["admin"]
    c, doc = doctor_client(app, admin)
    bad = c.post("/api/v1/auth/password/change", json={"current_password": "nope", "new_password": "Another-Pass-2026!"})
    assert bad.status_code == 422
    ok = c.post("/api/v1/auth/password/change", json={"current_password": NEW_PW, "new_password": "Another-Pass-2026!",
                                                      "confirm_password": "Another-Pass-2026!"})
    assert ok.status_code == 200
    # Forgot password: identical response for real and unknown IDs; admin is notified.
    f1 = TestClient(app).post("/api/v1/auth/password/forgot", json={"login_id": doc["doctor_id"]})
    f2 = TestClient(app).post("/api/v1/auth/password/forgot", json={"login_id": "DOC-UNKNOWN"})
    assert f1.status_code == f2.status_code == 202 and f1.json() == f2.json()
    detail = admin.get(f"/api/v1/admin/doctors/{doc['doctor_id']}").json()["doctor"]
    assert detail["password_reset_requested_at"] is not None
    # Admin initiates reset → code shown once; existing sessions end.
    pr = admin.post(f"/api/v1/admin/doctors/{doc['doctor_id']}/password-reset", json={"revoke_sessions": True}).json()
    code = pr["password_reset"]["reset_code"]
    assert c.get("/api/v1/auth/me").status_code == 401
    wrong = TestClient(app).post("/api/v1/auth/password/reset", json={
        "login_id": doc["doctor_id"], "reset_code": "ZZZZ-ZZZZ-ZZZZ", "new_password": "Third-Pass-2026!x",
        "confirm_password": "Third-Pass-2026!x"})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "RESET_INVALID"
    done = TestClient(app).post("/api/v1/auth/password/reset", json={
        "login_id": doc["doctor_id"], "reset_code": code, "new_password": "Third-Pass-2026!x",
        "confirm_password": "Third-Pass-2026!x"})
    assert done.status_code == 200
    assert TestClient(app).post("/api/v1/auth/login", json={"login_id": doc["doctor_id"],
                                                            "password": "Third-Pass-2026!x"}).status_code == 200
    reuse = TestClient(app).post("/api/v1/auth/password/reset", json={
        "login_id": doc["doctor_id"], "reset_code": code, "new_password": "Fourth-Pass-2026!x",
        "confirm_password": "Fourth-Pass-2026!x"})
    assert reuse.status_code == 422


# --- admin lifecycle -----------------------------------------------------------------------

def test_suspend_reactivate_deactivate_archive(app, clients, db):
    admin = clients["admin"]
    c, doc = doctor_client(app, admin)
    did = doc["doctor_id"]
    s = admin.post(f"/api/v1/admin/doctors/{did}/suspend", json={"reason": "Licence verification pending"}).json()
    assert s["doctor"]["account_status"] == "suspended" and s["sessions_revoked"] >= 1
    assert c.get("/api/v1/doctor/dashboard").status_code == 401  # suspended accounts lose access immediately
    r = TestClient(app).post("/api/v1/auth/login", json={"login_id": did, "password": NEW_PW})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
    assert admin.post(f"/api/v1/admin/doctors/{did}/reactivate", json={}).json()["doctor"]["account_status"] == "active"
    assert TestClient(app).post("/api/v1/auth/login", json={"login_id": did, "password": NEW_PW}).status_code == 200
    admin.post(f"/api/v1/admin/doctors/{did}/deactivate", json={"reason": "Left the hospital"})
    r = TestClient(app).post("/api/v1/auth/login", json={"login_id": did, "password": NEW_PW})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_INACTIVE"
    assert admin.delete(f"/api/v1/admin/doctors/{did}?reason=Records%20retention").json()["doctor"]["account_status"] == "archived"
    r = TestClient(app).post("/api/v1/auth/login", json={"login_id": did, "password": NEW_PW})
    assert r.status_code == 401  # archived accounts cannot authenticate
    assert db.query(User).filter_by(login_id=did).one()  # soft archive: row retained
    events = {e["event_type"] for e in admin.get(f"/api/v1/admin/audit-logs?doctor_id={did}&page_size=100").json()["items"]}
    assert {"doctor.create", "doctor.suspend", "doctor.reactivate", "doctor.deactivate", "doctor.archive",
            "auth.activation", "auth.login"} <= events


def test_admin_edit_detail_reset_activation_dashboard(app, clients):
    admin = clients["admin"]
    c, doc = doctor_client(app, admin)
    did = doc["doctor_id"]
    upd = admin.patch(f"/api/v1/admin/doctors/{did}", json={"designation": "Head of Unit", "department": "Cardiology"})
    assert upd.json()["doctor"]["designation"] == "Head of Unit" and upd.json()["doctor"]["department"] == "Cardiology"
    assert admin.patch(f"/api/v1/admin/doctors/{did}", json={"role": "admin"}).status_code == 422
    detail = admin.get(f"/api/v1/admin/doctors/{did}").json()
    assert detail["activation_history"][0]["state"] == "used" and detail["active_sessions"]
    assert any(e["event_type"] == "doctor.update" for e in detail["administrative_changes"])
    bad = admin.post(f"/api/v1/admin/doctors/{did}/reset-activation", json={"reason": "Lost device", "confirm_doctor_id": "WRONG"})
    assert bad.status_code == 422
    rs = admin.post(f"/api/v1/admin/doctors/{did}/reset-activation", json={"reason": "Lost device", "confirm_doctor_id": did}).json()
    assert rs["doctor"]["account_status"] == "pending_activation" and c.get("/api/v1/auth/me").status_code == 401
    _, again = activate(app, rs["doctor"], rs["activation"]["activation_code"], password="Fresh-Start-2026!x")
    assert again.status_code == 201
    dash = admin.get("/api/v1/admin/dashboard").json()
    assert dash["total_doctor_ids"] >= 1 and "pending_activations" in dash and dash["security_events"]
    lst = admin.get(f"/api/v1/admin/doctors?q={did}&status=active").json()
    assert [d["doctor_id"] for d in lst["items"]] == [did]
    assert admin.get("/api/v1/admin/doctors?sort=bogus:asc").status_code == 422


def test_bulk_import_preview_and_commit_and_export(clients):
    admin = clients["admin"]
    good = uniq()
    csv_bad = f"doctor_id,full_name,email,department\n{good},Dr. A,a.{good}@h.test,Medicine\n,X,not-an-email,Medicine\n"
    prev = admin.post("/api/v1/admin/doctors/import", files={"file": ("d.csv", csv_bad, "text/csv")}).json()
    assert prev["committed"] is False and prev["invalid_rows"] == 1 and prev["rows"][1]["errors"]
    refused = admin.post("/api/v1/admin/doctors/import?commit=true", files={"file": ("d.csv", csv_bad, "text/csv")}).json()
    assert refused["committed"] is False  # atomic: nothing created while any row is invalid
    assert admin.get(f"/api/v1/admin/doctors?q={good}").json()["pagination"]["total_items"] == 0
    csv_ok = f"doctor_id,full_name,email,department\n{good},Dr. A,a.{good}@h.test,Medicine\n,=HYPERLINK(1),b.{good}@h.test,Medicine\n"
    done = admin.post("/api/v1/admin/doctors/import?commit=true", files={"file": ("d.csv", csv_ok, "text/csv")}).json()
    assert done["committed"] is True and len(done["created"]) == 2 and done["created"][0]["activation_code"]
    out = admin.get("/api/v1/admin/doctors/export")
    assert out.headers["content-type"].startswith("text/csv") and good in out.text
    assert "'=HYPERLINK" in out.text  # formula injection neutralised


def test_admin_settings(clients):
    admin = clients["admin"]
    s = admin.patch("/api/v1/admin/settings", json={"activation_token_hours": 48}).json()
    assert s["activation_token_hours"] == 48
    assert admin.patch("/api/v1/admin/settings", json={"max_failed_logins": 1}).status_code == 422
    admin.patch("/api/v1/admin/settings", json={"activation_token_hours": 72})


def test_doctor_cannot_use_admin_apis_or_escalate(app, clients):
    c, doc = doctor_client(app, clients["admin"])
    for method, path in (("get", "/api/v1/admin/dashboard"), ("get", "/api/v1/admin/doctors"),
                         ("post", "/api/v1/admin/doctors"), ("get", "/api/v1/admin/audit-logs"),
                         ("patch", "/api/v1/admin/settings"), ("get", "/api/v1/admin/users")):
        r = getattr(c, method)(path, **({"json": {}} if method != "get" else {}))
        assert r.status_code in (403, 422) and (r.status_code == 403 or method != "get"), path
    assert c.patch("/api/v1/doctor/profile", json={"role": "admin"}).status_code == 422
    assert c.patch("/api/v1/doctor/profile", json={"doctor_id": "ADM001"}).status_code == 422
    assert c.get("/api/v1/auth/me").json()["user"]["role"] == "doctor"
    # Admin is kept out of the doctor workspace.
    assert clients["admin"].get("/api/v1/doctor/dashboard").status_code == 403


# --- doctor workspace & isolation -----------------------------------------------------------

def test_doctor_workspace_isolation(app, clients, db):
    from app.utils.sample_casesheet import build_sample_pdf
    from app.workers.worker import run_once
    admin = clients["admin"]
    a, doc_a = doctor_client(app, admin)
    b, doc_b = doctor_client(app, admin)
    pa = a.post("/api/v1/doctor/patients", json={"patient_ref": "MRN-A1", "display_name": "Synthetic A",
                                                 "age_years": 70, "gender": "female"}).json()["patient"]
    assert a.post("/api/v1/doctor/patients", json={"patient_ref": "MRN-A1"}).status_code == 409
    up = a.post(f"/api/v1/doctor/patients/{pa['id']}/documents",
                files={"file": ("cs.pdf", io.BytesIO(build_sample_pdf()), "application/pdf")})
    assert up.status_code == 202, up.text
    case_id, doc_id = up.json()["case_id"], up.json()["document_id"]
    run_once("test")
    # Owner sees everything.
    assert [p["id"] for p in a.get("/api/v1/doctor/patients").json()["items"]] == [pa["id"]]
    assert [c_["case_id"] for c_ in a.get("/api/v1/doctor/cases").json()["items"]] == [case_id]
    assert a.get(f"/api/v1/cases/{case_id}/timeline").status_code == 200
    dash = a.get("/api/v1/doctor/dashboard").json()
    assert dash["total_patients"] == 1 and dash["active_audit_cases"] == 1 and dash["recent_documents"]
    # Doctor B sees nothing and every guessed identifier returns 404.
    assert b.get("/api/v1/doctor/patients").json()["items"] == []
    assert b.get("/api/v1/doctor/cases").json()["items"] == []
    for path in (f"/api/v1/doctor/patients/{pa['id']}", f"/api/v1/doctor/patients/{pa['id']}/cases",
                 f"/api/v1/cases/{case_id}", f"/api/v1/cases/{case_id}/timeline", f"/api/v1/cases/{case_id}/findings",
                 f"/api/v1/documents/{doc_id}", f"/api/v1/documents/{doc_id}/download",
                 f"/api/v1/documents/{doc_id}/pages/1/text", f"/api/v1/cases/{case_id}/audit-report"):
        assert b.get(path).status_code == 404, path
    assert b.patch(f"/api/v1/doctor/patients/{pa['id']}", json={"notes": "x"}).status_code == 404
    assert b.post(f"/api/v1/doctor/patients/{pa['id']}/documents",
                  files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 404
    assert b.post("/api/v1/cases/upload", data={"patient_id": pa["id"]},
                  files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}).status_code == 404
    assert b.post("/api/v1/assistant/message", json={"case_id": case_id, "message": "summary"}).status_code == 404
    assert b.post(f"/api/v1/doctor/patients/{pa['id']}/share", json={"doctor_id": doc_b["doctor_id"]}).status_code == 404
    # Explicit sharing grants read access; revoking removes it.
    sh = a.post(f"/api/v1/doctor/patients/{pa['id']}/share", json={"doctor_id": doc_b["doctor_id"]})
    assert sh.status_code == 201
    assert b.get(f"/api/v1/cases/{case_id}").status_code == 200
    assert b.get("/api/v1/doctor/patients?scope=shared").json()["items"][0]["access"] == "read"
    assert b.patch(f"/api/v1/doctor/patients/{pa['id']}", json={"notes": "x"}).status_code == 404  # read-only share
    assert a.delete(f"/api/v1/doctor/patients/{pa['id']}/share/{doc_b['doctor_id']}").status_code == 200
    assert b.get(f"/api/v1/cases/{case_id}").status_code == 404
    # Archiving the owner keeps the patient and case (no cascading deletion).
    admin.delete(f"/api/v1/admin/doctors/{doc_a['doctor_id']}?reason=Retired")
    from app.models import Patient, PatientCase
    assert db.get(Patient, pa["id"]) is not None and db.get(PatientCase, case_id) is not None


def test_doctor_profile_and_preferences(app, clients):
    c, doc = doctor_client(app, clients["admin"])
    prof = c.get("/api/v1/doctor/profile").json()
    assert prof["profile"]["doctor_id"] == doc["doctor_id"] and "doctor_id" in prof["read_only_fields"]
    assert c.patch("/api/v1/doctor/profile", json={"phone": "+91 98765 43210"}).json()["profile"]["phone"] == "+91 98765 43210"
    assert c.patch("/api/v1/doctor/profile", json={"department": "Surgery"}).status_code == 422
    p = c.patch("/api/v1/doctor/preferences", json={"notification_preferences": {"case_processed": False}}).json()
    assert p["preferences"]["notification_preferences"]["case_processed"] is False
    assert p["preferences"]["notification_preferences"]["security_alerts"] is True
    acts = c.get("/api/v1/auth/activity").json()["items"]
    assert any(a["event_type"] == "auth.activation" for a in acts)


def test_audit_log_has_no_secrets(app, clients, db):
    d = provision(clients["admin"])
    code = d["activation"]["activation_code"]
    activate(app, d["doctor"], code)
    blob = " ".join(str(x) for (x,) in db.query(AuditLog.details).all())
    assert code not in blob and code.replace("-", "") not in blob and NEW_PW not in blob
    assert clients["admin"].get("/api/v1/admin/audit-logs/verify").json()["valid"] is True
