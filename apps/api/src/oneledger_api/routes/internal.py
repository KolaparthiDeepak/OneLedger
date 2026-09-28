"""Internal endpoints with separate authentication (scheduler credential), not user sessions."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header
from oneledger_db.models import Job
from oneledger_shared.errors import AuthenticationError, NotFoundError

from ..deps import Ctx, ReadAuth
from ..services.scheduler import tick

router = APIRouter(tags=["internal"])


@router.post("/api/internal/runner")
def runner(ctx: Ctx, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Invoked every minute by Supabase Cron / Vercel Cron. Bounded by JOB_TIME_BUDGET_SECONDS."""
    secret = ctx.settings.scheduler_secret.get_secret_value()
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not secret or not hmac.compare_digest(presented, secret):
        raise AuthenticationError(code="SCHEDULER_UNAUTHORIZED")
    return tick(ctx, ctx.settings.job_time_budget_seconds)


@router.get("/api/v1/jobs/{job_id}")
def job_status(job_id: uuid.UUID, a: ReadAuth) -> dict[str, Any]:
    job = a.db.get(Job, job_id)
    if job is None or job.owner_id != a.owner_id:
        raise NotFoundError()
    return {
        "id": str(job.id),
        "type": job.type,
        "status": job.status.value,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error_code": job.last_error_code,
        "result": job.result,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }
