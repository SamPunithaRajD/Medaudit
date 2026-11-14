"""Logging setup with redaction. Patient data, document content, tokens and secrets must never be logged."""
from __future__ import annotations

import contextvars
import logging
import re
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_REDACTIONS = [
    (re.compile(r"(?i)(password|passwd|secret|token|cookie|authorization|api[_-]?key)(\"?\s*[:=]\s*\"?)([^\s\",;]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"\b\d{10,}\b"), "[REDACTED_NUMBER]"),  # long identifiers / phone numbers
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        for pattern, repl in _REDACTIONS:
            msg = pattern.sub(repl, msg)
        record.msg, record.args = msg, ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_medaudit_configured", False):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.handlers = [handler]
    root.setLevel(level)
    root._medaudit_configured = True  # type: ignore[attr-defined]
    for noisy in ("httpx", "httpcore", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
