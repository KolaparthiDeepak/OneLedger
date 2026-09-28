"""Durable PostgreSQL job queue with leases.

* Enqueue inside the same DB transaction as the state change that requires it.
* ``claim_jobs`` (SECURITY DEFINER) atomically leases ready or expired jobs with SKIP LOCKED.
* Handlers write data, checkpoints and job status in one transaction guarded by the lease
  generation, so duplicate delivery or an expired lease can never double-apply work.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from oneledger_db.models import Job
from oneledger_db.session import owner_session
from oneledger_domain.enums import JobStatus
from oneledger_shared.errors import AppError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..context import AppContext

log = logging.getLogger("oneledger.jobs")

LEASE_SECONDS = 60
BACKOFF_BASE_SECONDS = 15


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    owner_id: uuid.UUID
    type: str
    lease_generation: int
    attempts: int


class LeaseLost(Exception):
    """The job was reclaimed by another worker; abandon without writing."""


class PermanentJobError(AppError):
    """Not retryable (validation problem); requires correction rather than blind retry."""


Handler = Callable[[AppContext, ClaimedJob, float], None]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


def enqueue(
    db: Session,
    owner_id: uuid.UUID,
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    delay_seconds: int = 0,
    max_attempts: int = 5,
) -> uuid.UUID:
    """Idempotent enqueue. Payload must contain identifiers only -- never financial data or secrets."""
    for value in payload.values():
        if not isinstance(value, (str, int, bool)) or (isinstance(value, str) and len(value) > 80):
            raise ValueError("job payloads may contain only short identifiers")
    stmt = (
        insert(Job)
        .values(
            id=uuid.uuid4(),
            owner_id=owner_id,
            type=job_type,
            status=JobStatus.QUEUED,
            payload=payload,
            idempotency_key=idempotency_key[:200],
            max_attempts=max_attempts,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
        )
        .on_conflict_do_nothing(index_elements=["owner_id", "idempotency_key"])
        .returning(Job.id)
    )
    new_id = db.execute(stmt).scalar()
    if new_id is not None:
        return new_id
    existing = db.scalars(
        select(Job).where(Job.owner_id == owner_id, Job.idempotency_key == idempotency_key[:200])
    ).one()
    return existing.id


@contextmanager
def lease_tx(ctx: AppContext, job: ClaimedJob) -> Iterator[tuple[Session, Job]]:
    """Owner-scoped transaction that holds the job row lock and verifies the lease generation."""
    with owner_session(ctx.sessions, job.owner_id) as db:
        row = db.scalars(select(Job).where(Job.id == job.id).with_for_update()).first()
        if row is None or row.lease_generation != job.lease_generation or row.status != JobStatus.RUNNING:
            raise LeaseLost()
        yield db, row


def finish(row: Job, result: dict[str, Any] | None = None) -> None:
    row.status = JobStatus.SUCCEEDED
    row.completed_at = datetime.now(UTC)
    row.lease_expires_at = None
    row.last_error_code = None
    if result is not None:
        row.result = result


def fail_now(row: Job, code: str, result: dict[str, Any] | None = None) -> None:
    """Mark the job failed without retrying (the cause needs the owner, e.g. no AI credit)."""
    row.status = JobStatus.FAILED
    row.completed_at = datetime.now(UTC)
    row.lease_expires_at = None
    row.last_error_code = code[:60]
    if result is not None:
        row.result = result


def continue_later(row: Job, checkpoint: dict[str, Any]) -> None:
    """Persist progress and requeue immediately for the next bounded chunk."""
    row.checkpoint = checkpoint
    row.status = JobStatus.QUEUED
    row.next_attempt_at = datetime.now(UTC)
    row.lease_expires_at = None
    row.attempts = max(row.attempts - 1, 0)  # progress chunks do not consume retry budget


def claim(ctx: AppContext, limit: int = 5) -> list[ClaimedJob]:
    with owner_session(ctx.sessions, None) as db:
        rows = (
            db.execute(text("SELECT * FROM oneledger.claim_jobs(:l, :s)"), {"l": limit, "s": LEASE_SECONDS})
            .mappings()
            .all()
        )
    return [ClaimedJob(r["job_id"], r["owner_id"], r["job_type"], r["lease_generation"], r["attempts"]) for r in rows]


def _record_failure(ctx: AppContext, job: ClaimedJob, code: str, retryable: bool) -> None:
    try:
        with lease_tx(ctx, job) as (_db, row):
            row.last_error_code = code[:60]
            row.lease_expires_at = None
            if retryable and row.attempts < row.max_attempts:
                delay = BACKOFF_BASE_SECONDS * (2 ** (row.attempts - 1)) * (0.5 + random.random())
                row.status = JobStatus.QUEUED
                row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            else:
                row.status = JobStatus.FAILED
                row.completed_at = datetime.now(UTC)
                on_fail = FAILURE_HOOKS.get(row.type)
                if on_fail:
                    on_fail(_db, row, code)
    except LeaseLost:
        pass


FAILURE_HOOKS: dict[str, Callable[[Session, Job, str], None]] = {}


def run_one(ctx: AppContext, job: ClaimedJob, deadline: float) -> None:
    fn = HANDLERS.get(job.type)
    started = time.monotonic()
    if fn is None:
        _record_failure(ctx, job, "UNKNOWN_JOB_TYPE", retryable=False)
        return
    try:
        fn(ctx, job, deadline)
        log.info(
            "job finished",
            extra={
                "job_id": str(job.id),
                "job_type": job.type,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
    except LeaseLost:
        log.warning("job lease lost", extra={"job_id": str(job.id), "job_type": job.type})
    except PermanentJobError as exc:
        _record_failure(ctx, job, exc.code, retryable=False)
        log.warning("job failed permanently", extra={"job_id": str(job.id), "error_code": exc.code})
    except AppError as exc:
        _record_failure(ctx, job, exc.code, retryable=False)
        log.warning("job failed", extra={"job_id": str(job.id), "error_code": exc.code})
    except Exception as exc:  # retryable by default; details are never logged (may contain data)
        code = getattr(exc, "code", None)
        retryable = bool(getattr(exc, "retryable", True))
        _record_failure(ctx, job, str(code or type(exc).__name__.upper()), retryable=retryable)
        log.exception(
            "job error",
            extra={"job_id": str(job.id), "job_type": job.type, "error_code": str(code or type(exc).__name__)},
        )


def run_until(ctx: AppContext, budget_seconds: float, *, max_jobs: int = 200) -> int:
    """Process claimed jobs until the time budget is spent. Returns jobs processed."""
    deadline = time.monotonic() + budget_seconds
    processed = 0
    while time.monotonic() < deadline and processed < max_jobs:
        batch = claim(ctx, limit=3)
        if not batch:
            break
        for job in batch:
            if time.monotonic() >= deadline:
                release(ctx, job)
                continue
            run_one(ctx, job, deadline)
            processed += 1
    return processed


def release(ctx: AppContext, job: ClaimedJob) -> None:
    """Return a leased but unstarted job to the queue without consuming an attempt."""
    try:
        with lease_tx(ctx, job) as (_db, row):
            row.status = JobStatus.QUEUED
            row.lease_expires_at = None
            row.attempts = max(row.attempts - 1, 0)
    except LeaseLost:
        pass
