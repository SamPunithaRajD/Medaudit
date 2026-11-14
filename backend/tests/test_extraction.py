from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.document_intelligence.extraction.extractor import (PageText, extract_events, extract_labs,
                                                            extract_medications, extract_note_entries,
                                                            extract_patient_fields, extract_vitals)
from app.document_intelligence.extraction.timestamps import parse_timestamp
from app.document_intelligence.validation.entities import SourceRef


def page(text, t="unknown", n=1):
    return PageText("DOC-1", n, t, text)


@pytest.mark.parametrize("s,expected,precision,basis", [
    ("14/10/2026 08:42", datetime(2026, 10, 14, 8, 42, tzinfo=timezone.utc), "minute", "explicit"),
    ("14 Oct 2026", datetime(2026, 10, 14, tzinfo=timezone.utc), "date", "explicit"),
    ("2026-10-14T10:05:30", datetime(2026, 10, 14, 10, 5, 30, tzinfo=timezone.utc), "second", "explicit"),
    ("8.30 pm", datetime(2026, 10, 14, 20, 30, tzinfo=timezone.utc), "minute", "page_context_date"),
])
def test_timestamp_normalisation(s, expected, precision, basis):
    r = parse_timestamp(s, date(2026, 10, 14))
    assert (r.value, r.precision, r.basis) == (expected, precision, basis)


def test_no_invented_times():
    assert parse_timestamp("10:42 given").value is None  # no date anywhere → null, raw kept
    assert parse_timestamp("10:42 given").raw == "10:42"
    assert parse_timestamp("Hb 12.50 g/dl").raw is None  # decimals are not times
    assert parse_timestamp("BP 120/80").value is None


def test_vitals_units_and_plausibility():
    v = extract_vitals(page("14/10/2026 09:15 HR 112 BP 88/54 SpO2 95% Temp 101.3 F RR 22\nHR 1180"))
    by = {(x.measurement_type, x.value): x for x in v}
    assert by[("bp_systolic", 88)].unit == "mmHg" and by[("spo2", 95)].status == "documented"
    temp = next(x for x in v if x.measurement_type == "temperature")
    assert temp.value == pytest.approx(38.5, abs=0.1) and temp.status == "normalized" and temp.original_unit == "°F"
    assert next(x for x in v if x.value == 1180).status == "uncertain"


def test_labs_reference_ranges_and_flags():
    labs = extract_labs(page("Sample collected: 14/10/2026 10:05\nReported on: 14/10/2026 11:30\n"
                             "Serum Lactate: 3.8 mmol/L (Ref 0.5-2.2) H\nSodium: 140 mmol/L (Ref 135-145)\nK: 6.1 mmol/L (3.5-5.1)"))
    by = {l.test_code: l for l in labs}
    assert by["lactate"].abnormal_basis == "documented_flag"
    assert by["sodium"].abnormal_flag is None
    assert by["potassium"].abnormal_flag == "high" and by["potassium"].abnormal_basis == "documented_reference_range"
    assert by["lactate"].resulted.at == datetime(2026, 10, 14, 11, 30, tzinfo=timezone.utc)


def test_medications_kinds():
    meds = extract_medications(page("14/10/2026 10:00 Inj Ceftriaxone 1 g IV BD ordered\n"
                                    "14/10/2026 10:40 Inj Ceftriaxone 1 g IV given\nHb 9.2 g/dl", "medication"))
    assert [m.record_kind for m in meds] == ["order", "administration"]
    assert meds[0].dose_value == 1 and meds[0].route == "IV" and meds[0].frequency == "BD"


def test_patient_fields_and_conflicts():
    fields = extract_patient_fields([page("Patient Name: Test Person\nAge: 70 years Sex: F\nK/C/O: COPD")])
    got = {f.field: f for f in fields}
    assert got["patient_name"].value == "Test Person" and got["gender"].normalized == {"gender": "female"}
    assert got["gender"].status == "normalized" and "presenting_complaint" not in got


def test_note_entries_and_signature_markers():
    entries = extract_note_entries(page("NOTES\n14/10/2026 09:10 Seen. Dr. Rao (sign)\nPatient reviewed, continue.\n"
                                        "14/10/2026 11:00 Reviewed"), "doctor_note", "t")
    assert [e.at is None for e in entries] == [False, True, False]
    assert [e.has_signature_marker for e in entries] == [True, False, False]


def test_events_skip_titles():
    ev = extract_events(page("CPR RECORD (CODE BLUE)\n16/10/2026 02:55 Cardiac arrest. CPR started."), "t")
    assert len(ev) == 1 and ev[0].subtype == "resuscitation"


def test_source_ref_validation():
    with pytest.raises(ValidationError):
        SourceRef(document_id="D", page_number=0, excerpt="x")
