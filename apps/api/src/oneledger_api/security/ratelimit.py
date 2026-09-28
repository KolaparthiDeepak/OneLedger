"""Fixed-window rate limiting stored in PostgreSQL so limits hold across serverless instances."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


def hit(db: Session, bucket: str, *, limit: int, window_seconds: int) -> bool:
    """Record one hit; returns ``False`` when the bucket is over its limit."""
    now = datetime.now(UTC)
    window_start = datetime.fromtimestamp(int(now.timestamp()) // window_seconds * window_seconds, UTC)
    count = db.execute(
        text(
            "INSERT INTO oneledger.rate_limit_counters (bucket, window_start, count) VALUES (:b, :w, 1) "
            "ON CONFLICT (bucket, window_start) DO UPDATE SET count = oneledger.rate_limit_counters.count + 1 "
            "RETURNING count"
        ),
        {"b": bucket[:200], "w": window_start},
    ).scalar_one()
    return int(count) <= limit


def purge_old(db: Session, older_than_seconds: int = 86400) -> None:
    db.execute(
        text("DELETE FROM oneledger.rate_limit_counters WHERE window_start < now() - make_interval(secs => :s)"),
        {"s": older_than_seconds},
    )
