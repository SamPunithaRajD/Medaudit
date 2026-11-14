"""Deterministic clinical text patterns. These recognise *documented* values only.

Plausibility ranges below are data-quality checks for OCR errors (e.g. "HR 1180"), NOT clinical
thresholds. Clinical thresholds live exclusively in approved, versioned hospital protocols.
"""
from __future__ import annotations

import re

I = re.I

# --- Vitals ------------------------------------------------------------------
VITAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "heart_rate": re.compile(r"\b(?:HR|heart\s*rate|pulse(?:\s*rate)?)\s*[:=\-]?\s*(?P<v>\d{2,4})(?!\d)\s*(?P<u>bpm|/\s?min|b/min|per\s*min)?", I),
    "blood_pressure": re.compile(r"\b(?:BP|blood\s*pressure|NIBP|IBP)\s*[:=\-]?\s*(?P<s>\d{2,4})\s*/\s*(?P<d>\d{2,4})(?!\d)\s*(?P<u>mm\s*hg)?", I),
    "spo2": re.compile(r"\b(?:SpO2|SpO₂|SPO2|Sp02|SaO2|O2\s*sat(?:uration)?|saturation)\s*[:=\-]?\s*(?P<v>\d{2,4})(?!\d)\s*(?P<u>%)?", I),
    "temperature": re.compile(r"\b(?:temp(?:erature)?)\s*[:=\-]?\s*(?P<v>\d{2,3}(?:\.\d{1,2})?)(?![\d.])\s*°?\s*(?P<u>[CF])?\b", I),
    "respiratory_rate": re.compile(r"\b(?:RR|resp(?:iratory)?\.?\s*rate|respirations?)\s*[:=\-]?\s*(?P<v>\d{1,3})(?![\d.])\s*(?P<u>/\s?min|bpm|breaths?/min)?", I),
    "mean_arterial_pressure": re.compile(r"\bMAP\s*[:=\-]?\s*(?P<v>\d{2,4})(?!\d)\s*(?P<u>mm\s*hg)?", I),
    "gcs": re.compile(r"\bGCS\s*[:=\-]?\s*(?P<v>\d{1,3})(?![\d.])(?:\s*/\s*15)?", I),
    "capillary_glucose": re.compile(r"\b(?:GRBS|CBG|RBS|capillary\s*glucose)\s*[:=\-]?\s*(?P<v>\d{2,4})(?!\d)\s*(?P<u>mg/dl)?", I),
}
VITAL_UNITS = {"heart_rate": "bpm", "bp_systolic": "mmHg", "bp_diastolic": "mmHg", "spo2": "%",
               "temperature": "°C", "respiratory_rate": "/min", "mean_arterial_pressure": "mmHg", "gcs": "score",
               "capillary_glucose": "mg/dL"}
PLAUSIBLE = {"heart_rate": (15, 300), "bp_systolic": (30, 300), "bp_diastolic": (10, 200), "spo2": (30, 100),
             "temperature": (25, 45), "respiratory_rate": (2, 80), "mean_arterial_pressure": (15, 250),
             "gcs": (3, 15), "capillary_glucose": (10, 1500)}

# --- Laboratory ----------------------------------------------------------------
LAB_ALIASES: dict[str, list[str]] = {
    "hemoglobin": ["haemoglobin", "hemoglobin", "hgb", "hb"],
    "wbc": ["total leucocyte count", "total leukocyte count", "white cell count", "total wbc", "wbc", "tlc"],
    "platelets": ["platelet count", "platelets", "platelet", "plt"],
    "creatinine": ["serum creatinine", "s. creatinine", "s.creatinine", "creatinine"],
    "urea": ["blood urea", "s. urea", "urea"],
    "bun": ["bun"],
    "sodium": ["serum sodium", "s. sodium", "sodium", "na+", "na"],
    "potassium": ["serum potassium", "s. potassium", "potassium", "k+", "k"],
    "chloride": ["chloride", "cl-"],
    "lactate": ["serum lactate", "s. lactate", "lactic acid", "lactate"],
    "crp": ["c-reactive protein", "crp"],
    "procalcitonin": ["procalcitonin", "pct"],
    "bilirubin_total": ["total bilirubin", "t. bilirubin", "bilirubin total", "bilirubin"],
    "ast": ["sgot", "ast"],
    "alt": ["sgpt", "alt"],
    "albumin": ["albumin"],
    "inr": ["pt/inr", "inr"],
    "troponin": ["hs-troponin i", "troponin i", "troponin t", "troponin", "trop i", "trop t"],
    "glucose": ["blood glucose", "plasma glucose", "fbs", "ppbs", "glucose"],
    "ph": ["ph"],
    "pco2": ["pco2", "paco2"],
    "po2": ["po2", "pao2"],
    "hco3": ["hco3", "bicarbonate"],
    "d_dimer": ["d-dimer", "d dimer"],
}
LAB_UNITS = (r"mmol/l|mg/dl|g/dl|g/l|µmol/l|umol/l|μmol/l|iu/l|u/l|ng/ml|ng/l|pg/ml|mg/l|meq/l|mmhg|%|"
             r"cells/cumm|/cumm|/mm3|x\s?10\^?3/µl|x\s?10\^?3/ul|x\s?10\^?9/l|10\^9/l|lakhs?/cumm|sec|seconds")
_alias_alt = "|".join(sorted({re.escape(a) for v in LAB_ALIASES.values() for a in v}, key=len, reverse=True))
LAB_LINE = re.compile(
    rf"(?<![A-Za-z])(?P<name>{_alias_alt})(?![A-Za-z])\s*[:=\-]?\s*(?P<value>[<>]?\s?\d+(?:\.\d+)?)\s*(?P<unit>{LAB_UNITS})?"
    rf"(?:\s*(?P<flag>\b(?:H|L|HIGH|LOW)\b|\*))?"
    rf"(?:[^\n\d]{{0,25}}?(?:\(|\[|ref(?:erence)?\.?\s*(?:range|interval)?\s*[:\-]?\s*)(?P<ref>[<>]?\s?\d+(?:\.\d+)?\s*(?:-|–|to)\s*\d+(?:\.\d+)?|[<>]\s?\d+(?:\.\d+)?)\s*[\)\]]?)?"
    rf"(?:\s*(?P<flag2>\b(?:H|L|HIGH|LOW)\b))?", I)
ALIAS_TO_CODE = {a.lower(): code for code, aliases in LAB_ALIASES.items() for a in aliases}
SAMPLE_TIME = re.compile(r"\b(?:sample\s*(?:collected|collection|drawn)|collected(?:\s*on|\s*at)?|collection\s*(?:date|time))\s*[:\-]?\s*(?P<v>.+)", I)
RESULT_TIME = re.compile(r"\b(?:reported(?:\s*on|\s*at)?|report(?:ed)?\s*(?:date|time)|result(?:ed)?\s*(?:on|at)|authori[sz]ed\s*(?:on|at))\s*[:\-]?\s*(?P<v>.+)", I)

# --- Medication ------------------------------------------------------------------
MED_FORM = r"(?:inj(?:ection)?|tab(?:let)?|cap(?:sule)?|syp|syrup|inf(?:usion)?|neb(?:ulisation)?|susp)"
MED_UNIT = r"(?:mcg/kg/min|mg/kg|mg/hr|ml/hr|units?/hr|mg|gm|g|mcg|µg|ug|ml|units?|iu|meq)"
MED_ROUTE = r"(?:iv|i\.v\.|im|i\.m\.|sc|s/c|subcut|po|p\.o\.|oral|sl|pr|neb|ng|rt|inh|topical)"
MED_FREQ = (r"(?:od|bd|bid|tds|tid|qid|qds|hs|sos|prn|stat|q\s?\d{1,2}\s?h(?:rly|ourly)?|\d{1,2}\s?(?:th)?\s?hourly|"
            r"once\s+daily|twice\s+daily|continuous(?:ly)?|infusion)")
MED_LINE = re.compile(
    rf"(?:(?P<form>\b{MED_FORM})\.?\s+)?(?P<name>[A-Za-z][A-Za-z0-9]+(?:[\-+/ ][A-Za-z][A-Za-z0-9]+){{0,3}}?)\s+"
    rf"(?P<dose>\d+(?:\.\d+)?)\s*(?P<unit>{MED_UNIT})(?![A-Za-z/])"
    rf"(?:\s*(?P<route>\b{MED_ROUTE})(?![A-Za-z]))?(?:\s*(?P<freq>\b{MED_FREQ})(?![A-Za-z]))?", I)
MED_ADMIN = re.compile(r"\b(given|administered|admin(?:istration)?\s*(?:at|time)|adm\.?\s*at|was\s+started\s+at|started\s+at|time\s+given|sign(?:ed)?\s+given)\b", I)
MED_ORDER = re.compile(r"\b(rx|advised|order(?:ed)?|prescribed|plan|start(?:ed)?|to\s+be\s+given|continue)\b", I)
MED_CHANGE = re.compile(r"\b(stop(?:ped)?|discontinu\w*|withh(?:o|e)ld|hold|increase[d]?|decrease[d]?|titrat\w*|changed?\s+to|tapered?|escalat\w*\s+to)\b", I)
MED_NOT_DRUGS = {"hb", "hemoglobin", "haemoglobin", "glucose", "sodium", "potassium", "urea", "creatinine", "lactate",
                 "weight", "wt", "height", "bp", "hr", "rr", "spo2", "temp", "gcs", "map", "page", "bed", "age",
                 "intake", "output", "urine", "fluid balance", "total"}

# --- Patient / admission fields ---------------------------------------------------
_STOP = r"(?=\s{2,}|\s+(?:age|sex|gender|uhid|mrn|ip\s*no|ipd\s*no|reg\.?\s*no|ward|bed|doa|dept|department|unit)\b\s*[:\-]|$)"
PATIENT_FIELDS: dict[str, re.Pattern[str]] = {
    "patient_name": re.compile(rf"(?im)^\s*(?:patient(?:'s)?\s*name|name\s*of\s*(?:the\s*)?patient|name)\s*[:\-]\s*(?P<v>[A-Za-z][A-Za-z .'\-]{{1,80}}?){_STOP}"),
    "patient_identifier": re.compile(r"(?i)\b(?:UHID|MRN|IP\s*No\.?|IPD\s*No\.?|Reg(?:istration)?\.?\s*No\.?|Hospital\s*No\.?|CR\s*No\.?)\s*[:\-#]?\s*(?P<v>[A-Z0-9][A-Z0-9\-/]{2,30})"),
    "age": re.compile(r"(?i)\bage\s*[:\-/]?\s*(?P<v>\d{1,3})\s*(?:y(?:ea)?rs?|y)?\b|\b(?P<v2>\d{1,3})\s*(?:y|yrs?|years)\s*/\s*(?:m|f|male|female)\b"),
    "gender": re.compile(r"(?i)\b(?:sex|gender)\s*[:\-]\s*(?P<v>male|female|m|f|other|transgender)\b|\b\d{1,3}\s*(?:y|yrs?|years)\s*/\s*(?P<v2>m|f|male|female)\b"),
    "admitted_at": re.compile(r"(?i)\b(?:DOA|date\s*(?:&|and)?\s*time\s*of\s*admission|date\s*of\s*admission|admitted\s*on|admission\s*(?:date|time|date\s*&\s*time))\s*[:\-]?\s*(?P<v>[^\n]{4,60})"),
    "outcome_at": re.compile(r"(?i)\b(?:DOD|date\s*(?:&|and)?\s*time\s*of\s*death|date\s*of\s*death|time\s*of\s*death|declared\s*dead\s*(?:on|at)|expired\s*on|date\s*(?:&\s*time\s*)?of\s*discharge)\s*[:\-]?\s*(?P<v>[^\n]{4,60})"),
    "department": re.compile(rf"(?im)\b(?:department|dept\.?|speciality|specialty)\s*[:\-]\s*(?P<v>[A-Za-z][A-Za-z &/\-]{{1,60}}?){_STOP}"),
    "ward": re.compile(rf"(?im)\b(?:ward|unit)\s*[:\-]\s*(?P<v>[A-Za-z0-9][A-Za-z0-9 &/\-]{{0,60}}?){_STOP}"),
    "cause_as_recorded": re.compile(r"(?im)\b(?:cause\s*of\s*death|immediate\s*cause(?:\s*of\s*death)?)\s*[:\-]\s*(?P<v>[^\n]{3,250})"),
    "diagnosis": re.compile(r"(?im)\b(?:final\s*diagnosis|provisional\s*diagnosis|working\s*diagnosis|diagnosis|impression|dx)\s*[:\-]\s*(?P<v>[^\n]{3,250})"),
    "presenting_complaint": re.compile(r"(?im)\b(?:presenting\s*complaints?|chief\s*complaints?|(?<![/\w])c/o|complaints?\s*of)\s*[:\-]?\s*(?P<v>[^\n]{3,250})"),
    "comorbidity": re.compile(r"(?im)\b(?:k/c/o|known\s*case\s*of|co-?morbidit(?:y|ies)|past\s*(?:medical\s*)?history)\s*[:\-]?\s*(?P<v>[^\n]{2,250})"),
    "icu_details": re.compile(r"(?im)\b(?:icu\s*(?:admission|details)\s*[:\-]|admitted\s*to\s*(?:the\s*)?(?:micu|sicu|icu|ccu)\b)\s*(?P<v>[^\n]{2,200})"),
}
DEATH_LOCATION = [
    ("brought_dead", re.compile(r"(?i)\bbrought\s*(?:in\s*)?dead\b|\bdead\s*on\s*arrival\b|\bDOA\b(?=\s*(?:case|patient))")),
    ("in_transit", re.compile(r"(?i)\b(?:died|expired|death)\s*(?:during|in)\s*transit\b")),
    ("in_hospital", re.compile(r"(?i)\b(?:in-?\s?hospital\s*death|died\s*in\s*(?:the\s*)?(?:hospital|ward|icu)|declared\s*dead\s*in\s*(?:the\s*)?(?:ward|icu|hospital))\b")),
]

# --- Events ----------------------------------------------------------------------
EVENT_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("cpr", "resuscitation", re.compile(r"\b(CPR|cardio\s*-?pulmonary\s*resuscitation|code\s*blue|cardiac\s*arrest|ROSC|defibrillat\w*|DC\s*shock|chest\s*compressions?|asystole|pulseless)\b", I)),
    ("other", "death_declared", re.compile(r"\b(declared\s*dead|death\s*declared|pronounced\s*dead|patient\s*expired|time\s*of\s*death)\b", I)),
    ("icu_escalation", "icu_transfer", re.compile(r"\b((?:shift(?:ed)?|transferr?(?:ed)?|moved)\s*to\s*(?:the\s*)?(?:MICU|SICU|ICU|CCU|HDU|intensive\s*care))\b", I)),
    ("icu_escalation", "icu_request", re.compile(r"\b((?:ICU|MICU|SICU|critical\s*care|intensivist)\s*(?:bed\s*)?(?:request(?:ed)?|referral|review\s*requested|call(?:ed)?)|(?:request(?:ed)?|referr(?:al|ed))\s*(?:for\s*)?(?:an?\s*)?(?:ICU|MICU|critical\s*care)\s*(?:bed|review|transfer)?)\b", I)),
    ("icu_escalation", "senior_informed", re.compile(r"\b((?:consultant|registrar|senior\s*resident|duty\s*doctor|RMO|on-?\s?call\s*doctor|physician|doctor|dr\.?\s*[A-Z][a-z]+)\s*(?:was\s*)?(?:informed|called|notified|intimated)|informed\s*(?:to\s*)?(?:the\s*)?(?:dr\.?|doctor|consultant|RMO|duty\s*doctor))\b", I)),
    ("emergency", "deterioration", re.compile(r"\b(deteriorat\w*|desaturat\w*|hypotensi\w*|unresponsive|GCS\s*(?:dropped|fell|decreased|reduced)|altered\s*sensorium|seizures?|convulsions?|rapid\s*response|emergency\s*call|collapsed?)\b", I)),
    ("procedure", "procedure", re.compile(r"\b(intubat\w*|mechanical\s*ventilation|put\s*on\s*ventilator|NIV|BiPAP|central\s*(?:venous\s*)?line|CVC\s*insert\w*|arterial\s*line|foley\w*|catheteri[sz]\w*|Ryles?\s*tube|tracheostomy|dialysis|haemodialysis|transfusion|chest\s*tube|ICD\s*insert\w*|lumbar\s*puncture)\b", I)),
]

SIGNATURE = re.compile(r"(?i)(signature|signed|sign\s*[:\-]|sd/-|\bsd\b|initials?\s*[:\-]|\bdr\.?\s+[a-z]{2,}|sister\s+[a-z]{2,}|staff\s*nurse\s*[:\-]?\s*[a-z]{2,}|\bSN\.?\s*[:\-]?\s*[a-z]{2,}|nurse\s*[:\-]\s*[a-z]{2,}|\(sign\))")
PAGE_NUMBERING = re.compile(r"(?i)\bpage\s*(?P<n>\d{1,3})\s*(?:of|/)\s*(?P<t>\d{1,3})\b")
