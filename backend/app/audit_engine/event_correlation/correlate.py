"""Temporal correlation helpers over timed events/observations."""
from __future__ import annotations

import operator
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import TypeVar

T = TypeVar("T")

OPS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge, "eq": operator.eq}


def compare(value: float | None, op: str, threshold: float) -> bool:
    if value is None or op not in OPS:
        return False
    return OPS[op](value, threshold)


def first_after(items: Iterable[T], when: datetime, key, *, within: timedelta | None = None,
                predicate=None) -> T | None:
    best = None
    for it in items:
        t = key(it)
        if t is None or t < when:
            continue
        if within is not None and t > when + within:
            continue
        if predicate is not None and not predicate(it):
            continue
        if best is None or t < key(best):
            best = it
    return best


def gaps(times: Sequence[datetime], max_gap: timedelta) -> list[tuple[int, int, timedelta]]:
    """Indices (i, i+1) of consecutive distinct timestamps further apart than max_gap."""
    out = []
    for i in range(len(times) - 1):
        d = times[i + 1] - times[i]
        if d > max_gap:
            out.append((i, i + 1, d))
    return out


def minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)
