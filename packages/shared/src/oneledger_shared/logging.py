"""Allowlist-based structured JSON logging.

Only fields in ``SAFE_FIELDS`` are emitted from ``extra``. Anything else (amounts,
descriptions, tokens, account numbers) is dropped rather than redacted after the fact.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SAFE_FIELDS = frozenset(
    {
        "request_id",
        "job_id",
        "job_type",
        "stage",
        "duration_ms",
        "status_code",
        "method",
        "route",
        "count",
        "fetched",
        "valid",
        "rejected",
        "inserted",
        "duplicates",
        "linked",
        "revised",
        "categorized",
        "review_required",
        "error_code",
        "attempt",
        "import_id",
        "tool",
        "provider",
        "model",
        "rounds",
        "event",
    }
)

_ACCOUNT_NUMBER = re.compile(r"\b\d{6,}\b")


def mask_account_number(value: str | None) -> str | None:
    """Return ``XXXXXX1234`` style masking; never returns more than four trailing characters."""
    if not value:
        return value
    compact = re.sub(r"[\s-]", "", value)
    if len(compact) <= 4:
        return "X" * len(compact)
    return "X" * min(len(compact) - 4, 8) + compact[-4:]


def scrub(text: str) -> str:
    """Defensive scrub for free text that reaches logs (e.g. exception class messages)."""
    return _ACCOUNT_NUMBER.sub(lambda m: mask_account_number(m.group(0)) or "", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key in SAFE_FIELDS and value is not None:
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only; messages/tracebacks may contain financial data.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).disabled = True
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
