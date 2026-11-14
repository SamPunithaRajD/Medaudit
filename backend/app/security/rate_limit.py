"""In-process sliding-window rate limiter for sensitive endpoints.

Single-process only; behind multiple API workers, replace with a shared store (e.g. Redis)
by implementing the same `hit()` interface.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.exceptions import RateLimited


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(window_seconds - (now - q[0])))
                raise RateLimited("Too many requests, retry later", headers={"Retry-After": str(retry)})
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()
