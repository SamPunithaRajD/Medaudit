"""Seed SYNTHETIC demonstration data: demo users, an illustrative protocol, and a processed synthetic case.

    python scripts/seed_demo.py            # uses DATABASE_URL from .env
All patients are fictional. The demo protocol is labelled as illustrative and must be replaced by
hospital-approved protocols before any real use. Demo users all get the password in DEMO_PASSWORD
(default printed once) and are flagged must_change_password.
"""
from __future__ import annotations

import io
import os
import secrets
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models import (CaseAssignment, DoctorProfile, HospitalProtocol, Patient, ProtocolVersion,  # noqa: E402
                        User)
from app.security import account_tokens  # noqa: E402
from app.services.case_service import create_case_from_upload  # noqa: E402
from app.services.processing_service import execute_job  # noqa: E402
from app.utils.ids import utcnow  # noqa: E402
from app.utils.sample_casesheet import build_sample_pdf  # noqa: E402
from app.workers.queue import queue  # noqa: E402

DEMO_USERS = [("DEMO-ADM", "demo.admin@medaudit.local", "Demo Administrator", "admin"),
              ("DEMO-DOC1", "demo.doctor@medaudit.local", "Dr. Demo Clinician", "doctor"),
              ("DEMO-NUR1", "demo.nurse@medaudit.local", "Demo Nurse Reviewer", "nurse"),
              ("DEMO-AUD1", "demo.auditor@medaudit.local", "Demo Quality Auditor", "auditor"),
              ("DEMO-COM1", "demo.committee@medaudit.local", "Demo Committee Member", "committee_member")]

DEMO_PROTOCOL_RULES = {"rules": [
    {"rule_id": "DEMO-OBS-INTERVAL", "type": "max_observation_interval", "category": "nursing_monitoring",
     "severity": "moderate", "title": "Observation interval longer than protocol maximum",
     "measurements": ["heart_rate", "bp_systolic", "spo2", "respiratory_rate"], "max_minutes": 240,
     "recommendation": {"category": "nursing", "priority": "moderate", "suggested_owner": "Nursing Superintendent",
                        "description": "Review observation frequency for unstable patients against the hospital monitoring protocol."}},
    {"rule_id": "DEMO-SBP-ESCALATION", "type": "escalation_after_abnormal", "category": "medical_management",
     "severity": "high", "title": "No documented escalation after low systolic blood pressure",
     "measurement": "bp_systolic", "operator": "lt", "value": 90, "max_minutes": 60,
     "escalation_types": ["icu_escalation", "doctor_note"],
     "recommendation": {"category": "treatment", "priority": "high", "suggested_owner": "Head of Department",
                        "description": "Audit escalation documentation for hypotension episodes and reinforce the escalation pathway."}},
    {"rule_id": "DEMO-LAB-REVIEW", "type": "lab_review_interval", "category": "medical_management", "severity": "moderate",
     "title": "Abnormal result without documented physician review", "tests": ["lactate", "creatinine"],
     "abnormal_only": True, "max_minutes": 120},
    {"rule_id": "DEMO-ICU-TRANSFER", "type": "event_interval", "category": "system_process", "severity": "moderate",
     "title": "ICU request-to-transfer interval exceeded", "from_subtype": "icu_request", "to_subtype": "icu_transfer",
     "max_minutes": 60,
     "recommendation": {"category": "system_process", "priority": "moderate", "suggested_owner": "Medical Superintendent",
                        "description": "Review ICU bed allocation and transfer timeliness."}},
    {"rule_id": "DEMO-MED-ADMIN", "type": "medication_administration_documented", "category": "documentation_quality",
     "severity": "low", "title": "Ordered medication without documented administration"},
    {"rule_id": "DEMO-DEATH-SUMMARY", "type": "required_documentation", "category": "documentation_quality",
     "severity": "moderate", "title": "Death summary not found", "page_types": ["death_summary"]},
]}


def main() -> None:
    from app.security.authentication import hash_password
    password = os.environ.get("DEMO_PASSWORD") or ("Demo-" + secrets.token_urlsafe(9) + "9!")
    with SessionLocal() as db:
        users = {}
        for login_id, email, name, role in DEMO_USERS:
            u = db.scalar(select(User).where(User.email == email))
            if u is None:
                u = User(login_id=login_id, email=email, display_name=name, role=role, department="Internal Medicine",
                         password_hash=hash_password(password), must_change_password=True, status="active",
                         is_activated=True, activated_at=utcnow())
                db.add(u)
                db.flush()
                if role == "doctor":
                    db.add(DoctorProfile(user_id=u.id, designation="Consultant (demo)"))
            users[role] = u
        db.flush()
        # A provisioned-but-not-activated doctor to demonstrate the activation flow.
        pending_code = None
        if db.scalar(select(User).where(User.login_id == "DEMO-DOC2")) is None:
            pend = User(login_id="DEMO-DOC2", email="demo.doctor2@medaudit.local", display_name="Dr. Demo Pending",
                        role="doctor", department="Internal Medicine", password_hash=None, status="pending_activation",
                        is_activated=False)
            db.add(pend)
            db.flush()
            db.add(DoctorProfile(user_id=pend.id, designation="Registrar (demo)", created_by=users["admin"].id))
            pending_code, _ = account_tokens.issue(db, pend, "activation", users["admin"].id)
        patient = Patient(owner_user_id=users["doctor"].id, patient_ref=f"SYN-{secrets.token_hex(3).upper()}",
                          display_name="Test Patient Alpha (synthetic)", age_years=68, gender="male",
                          department="Internal Medicine")
        db.add(patient)
        db.flush()
        if db.scalar(select(HospitalProtocol).where(HospitalProtocol.name == "DEMO monitoring & escalation protocol")) is None:
            p = HospitalProtocol(name="DEMO monitoring & escalation protocol", category="monitoring",
                                 responsible_department="Quality Department", created_by=users["admin"].id,
                                 description="ILLUSTRATIVE ONLY — synthetic thresholds for demonstration. Not clinically "
                                             "validated. Replace with hospital-approved protocols before use.")
            db.add(p)
            db.flush()
            db.add(ProtocolVersion(protocol_id=p.id, version="0.1-demo", status="approved", effective_date=date(2026, 1, 1),
                                   review_date=date(2027, 1, 1), rules=DEMO_PROTOCOL_RULES, created_by=users["admin"].id,
                                   approved_by=users["admin"].id, approved_at=utcnow(),
                                   change_note="Demonstration protocol"))
        db.commit()
        case, doc, job = create_case_from_upload(db, users["doctor"], filename="synthetic_case_sheet.pdf",
                                                 stream=io.BytesIO(build_sample_pdf(include_duplicate=True)),
                                                 department="Internal Medicine", patient_ref=patient.patient_ref,
                                                 metadata={"synthetic": True}, patient=patient)
        for role in ("nurse", "auditor", "committee_member"):
            db.add(CaseAssignment(case_id=case.id, user_id=users[role].id, assignment_role="reviewer"))
        db.commit()
        claimed = queue.claim(db, "seed")
    with SessionLocal() as db:
        job = execute_job(db, claimed or job.id, "seed")
        print(f"Synthetic case {case.id} processed: job {job.id} → {job.status}")
    print("Demo users (sign in with the ID; all must change password at first login):")
    for login_id, email, _, role in DEMO_USERS:
        print(f"  {role:17s} {login_id:10s} {email}")
    if pending_code:
        print(f"Pending doctor DEMO-DOC2 (demo.doctor2@medaudit.local) activation code (shown once): {pending_code}")
    if not os.environ.get("DEMO_PASSWORD"):
        print(f"Generated demo password (shown once): {password}")


if __name__ == "__main__":
    main()
