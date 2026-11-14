"""Generates an entirely SYNTHETIC case sheet (fictional patient) for tests and demonstrations.

Nothing here is real patient data. The record intentionally contains documentation features
(an untimed entry, an unsigned entry, a monitoring gap, a missing final page) so that the
pipeline's deterministic checks have something to find.
"""
from __future__ import annotations

import io
import zipfile

import fitz

BANNER = "SYNTHETIC DEMONSTRATION RECORD - FICTIONAL PATIENT - NOT FOR CLINICAL USE"
TOTAL = 9  # declared page count; page 9 is intentionally absent

PAGES: list[str] = [
    """EMERGENCY DEPARTMENT NOTES
Patient Name: Test Patient Alpha
Age: 68 years   Sex: Male   UHID: SYN-000148
Department: Internal Medicine
Ward: Medical ICU
DOA: 14/10/2026 08:42
Presenting complaints: fever for 3 days, breathlessness and altered sensorium
K/C/O: type 2 diabetes mellitus, systemic hypertension
Triage 14/10/2026 08:50 HR 118 bpm BP 92/58 mmHg SpO2 94% Temp 38.7 C RR 24/min
Provisional diagnosis: sepsis with suspected lower respiratory tract infection
Dr. Rao (sign)""",
    """DOCTOR'S PROGRESS NOTES
14/10/2026 09:10 Seen by RMO. O/E drowsy, warm peripheries. Impression: sepsis.
Plan: blood cultures, serum lactate, IV fluids. Dr. Rao (sign)
14/10/2026 13:20 Persistent hypotension despite fluid resuscitation, BP 86/50.
ICU bed requested. Consultant informed. Dr. Rao (sign)
Patient reviewed, continue same treatment.
15/10/2026 03:30 Called for hypotension. MAP 55. Noradrenaline dose increased.
Reviewed ABG, plan for NIV if worsens.""",
    """OBSERVATION CHART (TPR CHART) - VITAL SIGNS
14/10/2026 09:15 HR 112 BP 88/54 SpO2 95% Temp 38.4 C RR 22
14/10/2026 11:15 HR 110 BP 90/56 SpO2 95% Temp 38.2 C RR 22
14/10/2026 14:20 HR 105 BP 96/60 SpO2 96% RR 20
14/10/2026 18:20 HR 108 BP 94/58 SpO2 95% Temp 38.0 C RR 22
15/10/2026 02:35 HR 126 BP 78/44 MAP 55 SpO2 86% RR 30
15/10/2026 07:40 HR 122 BP 84/50 SpO2 90% RR 28""",
    """LABORATORY REPORT - BIOCHEMISTRY / HAEMATOLOGY
Sample collected: 14/10/2026 10:05
Reported on: 14/10/2026 11:30
Serum Lactate: 3.8 mmol/L (Ref 0.5-2.2) H
S. Creatinine: 2.1 mg/dL (Ref 0.6-1.2)
Sodium: 132 mmol/L (Ref 135-145)
Hb: 10.2 g/dL (Ref 13-17)
WBC: 18.4 x10^3/uL (Ref 4-11)
Procalcitonin: 12.5 ng/mL (Ref <0.5)""",
    """MEDICATION ADMINISTRATION RECORD (MAR)
14/10/2026 10:00 Inj Piperacillin-Tazobactam 4.5 g IV q8h ordered
14/10/2026 10:42 Inj Piperacillin-Tazobactam 4.5 g IV given by SN Mary
14/10/2026 14:30 Inf Noradrenaline 0.1 mcg/kg/min IV continuous started at 14:30
Inj Pantoprazole 40 mg IV OD
Tab Paracetamol 650 mg PO SOS""",
    """NURSING NOTES
14/10/2026 14:10 Patient shifted to ICU on monitor. SN Mary
15/10/2026 02:35 Patient desaturating, SpO2 86%, BP 78/44. Duty doctor informed. SN Asha
15/10/2026 06:00 Patient on NIV, urine output low. SN Asha""",
    """CPR / RESUSCITATION RECORD (CODE BLUE)
16/10/2026 02:55 Cardiac arrest noted. Code blue called. CPR started.
16/10/2026 03:15 No ROSC after 20 minutes of resuscitation.
16/10/2026 03:18 Patient declared dead by Dr. Iyer (sign)""",
    """DEATH SUMMARY
Patient Name: Test Patient Alpha
Age: 68 years   Sex: Male   UHID: SYN-000148
Date of admission: 14/10/2026 08:42
Date & time of death: 16/10/2026 03:18
Cause of death: Refractory septic shock with multi-organ dysfunction
Final diagnosis: sepsis with septic shock, acute kidney injury
Declared dead in the ICU.
Dr. Iyer (sign)""",
]


def build_sample_pdf(include_duplicate: bool = False) -> bytes:
    doc = fitz.open()
    pages = [(i, body) for i, body in enumerate(PAGES, start=1)]
    if include_duplicate:
        pages.append((3, PAGES[2]))  # exact re-scan of page 3
    for number, body in pages:
        page = doc.new_page(width=595, height=842)  # A4 points
        page.insert_text((40, 30), BANNER, fontsize=7)
        page.insert_textbox(fitz.Rect(40, 50, 555, 800), body, fontsize=10.5, fontname="helv")
        page.insert_text((470, 820), f"Page {number} of {TOTAL}", fontsize=8)
    out = doc.tobytes()
    doc.close()
    return out


def build_sample_zip_of_images(dpi: int = 150, pages: int = 3) -> bytes:
    pdf = fitz.open(stream=build_sample_pdf(), filetype="pdf")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(min(pages, pdf.page_count)):
            pix = pdf.load_page(i).get_pixmap(dpi=dpi)
            zf.writestr(f"scan_{i + 1:03d}.png", pix.tobytes("png"))
    pdf.close()
    return buf.getvalue()
