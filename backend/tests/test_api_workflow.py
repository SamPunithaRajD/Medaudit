"""End-to-end API workflow: upload → background processing → clinical data → audit → review → report → analytics."""
import pytest

from tests.conftest import upload_and_process, user_id


@pytest.fixture()
def processed(clients, db, demo_protocol):
    return upload_and_process(clients["doctor"], db)


def test_health_and_system(clients):
    c = clients["admin"]
    assert c.get("/health").json() == {"status": "ok"}
    st = c.get("/api/v1/system/status").json()
    assert st["database"]["connected"] and st["encryption"]["algorithm"] == "AES-256-GCM"
    ol = c.get("/api/v1/system/ollama-status").json()
    assert ol["local_only_ai"] is True and ol["enabled"] is False


def test_processing_status_and_job(clients, processed):
    c = clients["doctor"]
    st = c.get(f"/api/v1/cases/{processed['case_id']}/status").json()
    assert st["status"] == "completed" and st["progress_percent"] == 100.0
    assert [s["key"] for s in st["stages"]][:3] == ["ingest", "preprocess", "ocr"]
    assert all(s["status"] == "completed" for s in st["stages"])
    assert st["processing_state"] == "verification_pending"
    agents = c.get(f"/api/v1/jobs/{processed['job_id']}/agents").json()["agents"]
    names = {a["agent"] for a in agents}
    assert {"patient_information", "clinical_notes", "vitals", "medication", "laboratory", "emergency_cpr", "nursing",
            "documentation", "clinical_timeline", "evidence_verification", "report_generation"} <= names
    assert all(a["status"] == "completed" for a in agents)


def test_case_detail_and_list(clients, processed):
    c = clients["doctor"]
    d = c.get(f"/api/v1/cases/{processed['case_id']}").json()
    case = d["case"]
    assert case["patient"]["age_years"] == 68 and case["patient"]["gender"] == "male"
    assert case["admitted_at"] == "2026-10-14T08:42:00Z" and case["death_location"] == "in_hospital"
    assert case["field_provenance"]["age"]["page"] in (1, 8)
    assert d["finding_count"] > 0
    lst = c.get("/api/v1/cases?page_size=5&q=SYN-T").json()
    assert lst["pagination"]["page_size"] == 5 and any(x["id"] == processed["case_id"] for x in lst["items"])
    assert c.get("/api/v1/cases?sort=bogus:asc").status_code == 422


def test_documents_pages_and_evidence(clients, processed):
    c = clients["doctor"]
    docs = c.get(f"/api/v1/cases/{processed['case_id']}/documents").json()["items"]
    assert docs[0]["page_count"] == 9 and docs[0]["status"] == "ready"
    doc_id = docs[0]["id"]
    detail = c.get(f"/api/v1/documents/{doc_id}?page_size=10").json()
    pages = detail["pages"]["items"]
    assert pages[8]["duplicate_of_page"] == 3 and pages[2]["page_type"] == "vitals"
    txt = c.get(f"/api/v1/documents/{doc_id}/pages/3/text?include_words=true").json()
    assert "TPR CHART" in txt["text"] and txt["words"]
    png = c.get(f"/api/v1/documents/{doc_id}/pages/1/preview")
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    assert c.get(f"/api/v1/documents/{doc_id}/pages/99").status_code == 404
    dl = c.get(f"/api/v1/documents/{doc_id}/download")
    assert dl.content.startswith(b"%PDF") and dl.headers["cache-control"].startswith("no-store")


def test_clinical_data_endpoints(clients, processed):
    c, cid = clients["doctor"], processed["case_id"]
    tl = c.get(f"/api/v1/cases/{cid}/timeline?page_size=100").json()
    items = tl["items"]
    timed = [e["occurred_at"] for e in items if e["occurred_at"]]
    assert timed == sorted(timed)
    first_untimed = next(i for i, e in enumerate(items) if not e["occurred_at"])
    assert all(not e["occurred_at"] for e in items[first_untimed:])  # untimed listed after timed
    assert all(e["evidence"] and e["evidence"][0]["page_number"] for e in items)
    cpr = c.get(f"/api/v1/cases/{cid}/timeline?type=cpr").json()["items"]
    assert cpr and all(e["type"] == "cpr" for e in cpr)
    vit = c.get(f"/api/v1/cases/{cid}/vitals?measurement=spo2").json()["items"]
    assert vit and all(v["measurement"]["unit"] == "%" for v in vit)
    meds = c.get(f"/api/v1/cases/{cid}/medications").json()["items"]
    assert {m["record_kind"] for m in meds} >= {"order", "administration"}
    labs = c.get(f"/api/v1/cases/{cid}/laboratory?abnormal_only=true").json()["items"]
    lact = next(l for l in labs if l["test_code"] == "lactate")
    assert lact["abnormal_basis"] == "documented_flag" and lact["evidence"][0]["page_number"] == 4


def test_findings_from_protocol_and_builtin(clients, processed):
    c = clients["doctor"]
    fs = c.get(f"/api/v1/cases/{processed['case_id']}/findings?page_size=100").json()["items"]
    rules = {f["rule"]["rule_id"] for f in fs}
    assert "DEMO-OBS-INTERVAL" in rules and "DEMO-SBP-ESCALATION" in rules
    assert any(r.startswith("MEDAUDIT-DOC-INTEGRITY") for r in rules)
    for f in fs:
        assert f["requires_human_verification"] and f["review_status"] == "pending" and f["confidence"] is None
        assert f["rule"]["protocol_version"]
    gap = next(f for f in fs if f["rule"]["rule_id"] == "DEMO-OBS-INTERVAL")
    assert gap["verification_status"] == "verified" and {e["page_number"] for e in gap["evidence"]} == {3}
    # The protocol version used is identified on every protocol finding.
    assert gap["rule"]["protocol_version"] == "1.0"


def test_review_flow_report_and_preventability(clients, processed, db):
    doc, nurse, committee = clients["doctor"], clients["nurse"], clients["committee_member"]
    cid = processed["case_id"]
    fs = doc.get(f"/api/v1/cases/{cid}/findings").json()["items"]
    f0 = fs[0]
    # Nurse may comment but not accept.
    assert nurse.post(f"/api/v1/findings/{f0['id']}/review", json={"action": "accept"}).status_code == 403
    r = nurse.post(f"/api/v1/findings/{f0['id']}/review", json={"action": "comment", "comment": "Chart page checked"})
    assert r.status_code == 200 and r.json()["case_review_status"] == "under_review"
    # Stale update → 409
    assert doc.post(f"/api/v1/findings/{f0['id']}/review",
                    json={"action": "accept", "expected_updated_at": "2020-01-01T00:00:00Z"}).status_code == 409
    r = doc.post(f"/api/v1/findings/{f0['id']}/review", json={"action": "amend", "amended_title": "Amended title",
                                                              "comment": "Clarified"})
    body = r.json()
    assert body["finding"]["title"] == "Amended title" and body["finding"]["original"]["title"] == f0["title"]
    assert body["review"]["actor"]["user_id"] == user_id(db, "doctor")
    hist = doc.get(f"/api/v1/findings/{f0['id']}").json()["review_history"]
    assert [h["action"] for h in hist] == ["comment", "amend"]

    pv = doc.get(f"/api/v1/cases/{cid}/preventability").json()
    assert pv["preliminary"]["category"] in ("possibly_preventable", "undetermined")
    assert pv["preliminary"]["score_is_probability"] is False and pv["committee_decision"] is None
    assert pv["preliminary"]["framework_version"] == "1.0.0"
    # Doctor cannot record committee decision.
    assert doc.post(f"/api/v1/cases/{cid}/preventability/review", json={
        "decision_type": "committee_decision", "category": "preventable", "rationale": "x" * 20}).status_code == 403
    assert doc.post(f"/api/v1/cases/{cid}/preventability/review", json={
        "decision_type": "clinical_review", "category": "possibly_preventable",
        "rationale": "Clinical reviewer assessment of the evidence."}).json()["case_review_status"] == "committee_review"
    r = committee.post(f"/api/v1/cases/{cid}/preventability/review", json={
        "decision_type": "committee_decision", "category": "possibly_preventable",
        "rationale": "Committee consensus after review.", "supporting_finding_ids": [f0["id"]]})
    assert r.status_code == 201 and r.json()["case_review_status"] == "approved"

    regen = doc.post(f"/api/v1/cases/{cid}/audit-report/regenerate").json()
    rep = doc.get(f"/api/v1/cases/{cid}/audit-report").json()
    assert rep["report_version"] == regen["report_version"] >= 2
    assert rep["assessment"]["preliminary"] is True
    assert rep["sections"]["16_final_committee_conclusion"]["category"] == "possibly_preventable"
    assert rep["sections"]["13_preliminary_preventability_assessment"]["assessment_type"] == "preliminary"
    assert len([k for k in rep["sections"]]) == 16
    pdf = doc.get(f"/api/v1/cases/{cid}/audit-report/export?format=pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    # Analytics now include the committee-approved case.
    ov = clients["auditor"].get("/api/v1/analytics/overview").json()
    assert ov["total_deaths_reviewed"] >= 1 and ov["preventability_distribution"]["possibly_preventable"] >= 1
    assert "label" in ov["preliminary_preventability_distribution"]
    assert "patient" not in str(ov).lower().replace("patient_ref", "")


def test_recommendations_and_actions(clients, processed, db):
    c, cid = clients["doctor"], processed["case_id"]
    recs = c.get(f"/api/v1/cases/{cid}/recommendations").json()["items"]
    assert recs and all(r["finding_id"] and r["source"] in ("protocol_template", "builtin_template") for r in recs)
    r = c.post("/api/v1/actions", json={"case_id": cid, "category": "staff_training", "priority": "high",
                                        "description": "Escalation pathway training for ward staff",
                                        "owner_id": user_id(db, "nurse")})
    assert r.status_code == 201
    act = r.json()["recommendation"]
    assert act["tracked_as_action"] and act["status"] == "open" and act["due_date"]
    assert c.patch(f"/api/v1/actions/{act['id']}", json={"status": "reopened"}).status_code == 422  # invalid transition
    upd = c.patch(f"/api/v1/actions/{act['id']}", json={"status": "in_progress", "reviewer_comment": "Started"}).json()
    assert upd["recommendation"]["status"] == "in_progress" and upd["recommendation"]["reviewer_comments"]
    c.patch(f"/api/v1/actions/{act['id']}", json={"status": "complete"})
    hist = c.get(f"/api/v1/actions/{act['id']}/history").json()["items"]
    assert [h["to_status"] for h in hist] == ["open", "in_progress", "complete"]
    assert any(a["id"] == act["id"] for a in c.get("/api/v1/actions").json()["items"])


def test_reviews_collaboration(clients, processed, db):
    c, cid = clients["doctor"], processed["case_id"]
    rv = c.post("/api/v1/reviews", json={"case_id": cid, "title": "Case review", "assignee_id": user_id(db, "nurse")}).json()["review"]
    clients["nurse"].post(f"/api/v1/reviews/{rv['id']}/comments", json={"body": "Nursing charts reviewed"})
    assert clients["nurse"].post(f"/api/v1/reviews/{rv['id']}/decision", json={"decision": "approve"}).status_code == 403
    d = c.post(f"/api/v1/reviews/{rv['id']}/decision", json={"decision": "escalate_to_committee"}).json()["review"]
    assert d["status"] == "committee_review" and len(d["comments"]) == 1
    assert any(x["id"] == rv["id"] for x in clients["nurse"].get("/api/v1/reviews?mine=true").json()["items"])


def test_protocol_management_and_compare(clients, processed, demo_protocol):
    admin, doc = clients["admin"], clients["doctor"]
    bad = admin.post("/api/v1/protocols", json={"name": "Bad", "category": "x", "version": "1",
                                                 "rules": {"rules": [{"type": "max_observation_interval"}]}})
    assert bad.status_code == 422
    assert doc.post("/api/v1/protocols", json={"name": "N", "category": "x", "version": "1", "rules": {}}).status_code == 403
    p = admin.post("/api/v1/protocols", json={
        "name": "ICU monitoring v-test", "category": "monitoring", "version": "2.0", "rules": {"rules": [
            {"rule_id": "ICU-1", "type": "max_observation_interval", "category": "nursing_monitoring", "severity": "low",
             "title": "Gap", "measurements": ["heart_rate"], "max_minutes": 60}]}}).json()["protocol"]
    assert p["approved_status"] == "draft"
    ver = p["versions"][0]["id"]
    cmp = doc.post(f"/api/v1/protocols/{p['id']}/compare", json={"case_id": processed["case_id"], "version_id": ver}).json()
    assert cmp["protocol_version"] == "2.0" and cmp["persisted"] is False and cmp["deviations"]
    admin.post(f"/api/v1/protocols/{p['id']}/versions/{ver}/approve")
    assert admin.get(f"/api/v1/protocols/{p['id']}").json()["protocol"]["current_version"]["version"] == "2.0"


def test_assistant_and_counterfactual(clients, processed):
    c, cid = clients["doctor"], processed["case_id"]
    r = c.post("/api/v1/assistant/message", json={"case_id": cid, "message": "Summarize the clinical course"}).json()
    msg = r["message"]
    assert msg["provisional"] and msg["evidence"] and msg["limitations"] and "Admission" in msg["content"]
    conv = r["conversation_id"]
    gaps = c.post("/api/v1/assistant/message", json={"case_id": cid, "conversation_id": conv,
                                                     "message": "Identify major documentation gaps"}).json()["message"]
    assert "documented time" in gaps["content"] or "signature" in gaps["content"]
    adv = c.post("/api/v1/assistant/message", json={"case_id": cid, "message": "What dose should we give?"}).json()["message"]
    assert "can't provide treatment advice" in adv["content"]
    hist = c.get(f"/api/v1/assistant/conversations/{conv}").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    # Conversations are private to their author.
    assert clients["nurse"].get(f"/api/v1/assistant/conversations/{conv}").status_code == 404
    cf = c.post(f"/api/v1/cases/{cid}/counterfactual", json={"scenario_type": "earlier_escalation",
                                                             "question": "What if escalation had occurred earlier?",
                                                             "minutes_earlier": 60}).json()
    assert cf["hypothetical"] is True and "HYPOTHETICAL" in cf["label"] and cf["assumptions"] and cf["limitations"]
    assert "outcome" in " ".join(cf["uncertainty"]).lower()
