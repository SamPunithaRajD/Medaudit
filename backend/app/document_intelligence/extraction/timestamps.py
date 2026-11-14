"""Timestamp detection and normalisation.

Rules (non-negotiable): never invent a date or time. A time without a date on the same line is
only combined with a date that is explicitly written earlier on the *same page*; such values are
marked timestamp_basis="page_context_date" and treated as uncertain. Otherwise the timestamp is null
and the raw text is preserved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.core.config import get_settings

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTHS.update({"sept": 9, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
               "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

DATE_PATTERNS = [
    ("iso", re.compile(r"\b(?P<y>(?:19|20)\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?!\d)")),
    ("dmy", re.compile(r"\b(?P<d>\d{1,2})[/.\-](?P<m>\d{1,2})[/.\-](?P<y>(?:19|20)?\d{2})\b")),
    ("d_mon_y", re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s\-/]*(?P<mon>{_MON})\.?[\s\-/,]*(?P<y>(?:19|20)\d{{2}})\b", re.I)),
    ("mon_d_y", re.compile(rf"\b(?P<mon>{_MON})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d{{2}})\b", re.I)),
]
TIME_PATTERNS = [
    re.compile(r"(?<![\d/.\-])(?P<h>[01]?\d|2[0-3]):(?P<mi>[0-5]\d)(?::(?P<s>[0-5]\d))?\s*(?P<ampm>[ap]\.?\s?m\.?)?(?![\d:])", re.I),
    re.compile(r"(?<![\d/.\-])(?P<h>[01]?\d|2[0-3])\.(?P<mi>[0-5]\d)\s*(?:hrs?\b|(?P<ampm>[ap]\.?m\.?))", re.I),
    re.compile(r"(?<!\d)(?P<h>[01]\d|2[0-3])(?P<mi>[0-5]\d)\s*(?:hrs?|hours)\b", re.I),
    re.compile(r"(?<!\d)(?P<h>1[0-2]|0?[1-9])\s*(?P<ampm>[ap]\.?m\.?)(?![a-z])", re.I),
]


@dataclass
class TimeMatch:
    value: datetime | None
    precision: str  # second|minute|hour|date|unknown
    basis: str  # explicit | page_context_date | none
    raw: str | None
    has_date: bool
    has_time: bool
    date_value: date | None = None


@lru_cache(maxsize=4)
def _tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def hospital_tz() -> ZoneInfo:
    return _tz(get_settings().hospital_timezone)


def _year(y: str) -> int:
    yi = int(y)
    return yi + 2000 if yi < 100 else yi


def find_dates(text: str) -> list[tuple[date, tuple[int, int], str]]:
    found: list[tuple[date, tuple[int, int], str]] = []
    taken: list[tuple[int, int]] = []
    for kind, pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            span = m.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in taken):
                continue
            try:
                d = int(m.group("d"))
                if m.groupdict().get("mon"):
                    mon = m.group("mon").lower().rstrip(".")
                    mo = MONTHS.get(mon) or MONTHS[mon[:3]]
                else:
                    mo = int(m.group("m"))
                dv = date(_year(m.group("y")), mo, d)
            except (ValueError, KeyError):
                continue
            if not (1990 <= dv.year <= 2100):
                continue
            taken.append(span)
            found.append((dv, span, m.group(0)))
    found.sort(key=lambda x: x[1][0])
    return found


def find_time(text: str, exclude: list[tuple[int, int]] | None = None) -> tuple[time, str, str] | None:
    exclude = exclude or []
    for pat in TIME_PATTERNS:
        for m in pat.finditer(text):
            if any(not (m.end() <= a or m.start() >= b) for a, b in exclude):
                continue
            h = int(m.group("h"))
            mi = int(m.groupdict().get("mi") or 0)
            sec = m.groupdict().get("s")
            ampm = (m.groupdict().get("ampm") or "").lower().replace(".", "").replace(" ", "")
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            if h > 23:
                continue
            precision = "second" if sec else ("minute" if m.groupdict().get("mi") else "hour")
            return time(h, mi, int(sec or 0)), precision, m.group(0).strip()
    return None


def to_utc(d: date, t: time | None) -> datetime:
    local = datetime.combine(d, t or time(0, 0), tzinfo=hospital_tz())
    return local.astimezone(timezone.utc)


def parse_timestamp(text: str, context_date: date | None = None) -> TimeMatch:
    dates = find_dates(text)
    spans = [s for _, s, _ in dates]
    t = find_time(text, exclude=spans)
    if dates:
        dv, _, raw_d = dates[0]
        if t:
            tv, precision, raw_t = t
            return TimeMatch(to_utc(dv, tv), precision, "explicit", f"{raw_d} {raw_t}", True, True, dv)
        return TimeMatch(to_utc(dv, None), "date", "explicit", raw_d, True, False, dv)
    if t:
        tv, precision, raw_t = t
        if context_date is not None:
            return TimeMatch(to_utc(context_date, tv), precision, "page_context_date", raw_t, False, True, context_date)
        return TimeMatch(None, "unknown", "none", raw_t, False, True)
    return TimeMatch(None, "unknown", "none", None, False, False)


def parse_value(value: str) -> TimeMatch:
    """Parse a labelled field value such as 'DOA: 14/10/2026 08:42'."""
    return parse_timestamp(value)
