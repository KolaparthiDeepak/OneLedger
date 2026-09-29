"""Scheduler tick: plan due work idempotently, then drain the queue within a time budget.

Called by the protected runner endpoint (hosted: Supabase Cron / Vercel Cron every minute) and by
the local worker loop. Planning uses unique idempotency keys, so overlapping ticks are harmless.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from oneledger_db.models import User
from oneledger_db.session import owner_session
from oneledger_domain.periods import today_in
from sqlalchemy import text

from ..context import AppContext
from ..security.ratelimit import purge_old
from .jobs import ClaimedJob, enqueue, finish, handler, lease_tx, run_until

log = logging.getLogger("oneledger.scheduler")

MAINTENANCE_JOB = "maintenance.daily"


def _owners(ctx: AppContext) -> list[uuid.UUID]:
    with owner_session(ctx.sessions, None) as db:
        return list(db.execute(text("SELECT * FROM oneledger.enabled_owner_ids()")).scalars())


def plan(ctx: AppContext) -> dict[str, int]:
    planned = {"maintenance": 0, "phone_alerts": 0}
    for owner_id in _owners(ctx):
        with owner_session(ctx.sessions, owner_id) as db:
            user = db.get(User, owner_id)
            tz = user.timezone if user else ctx.settings.default_timezone
            day = today_in(tz)
            enqueue(db, owner_id, MAINTENANCE_JOB, {"day": day.isoformat()}, f"maintenance:{day.isoformat()}")
            planned["maintenance"] += 1
        planned["phone_alerts"] += _phone_alerts(ctx, owner_id)
    return planned


def _phone_alerts(ctx: AppContext, owner_id: uuid.UUID) -> int:
    """Push new alerts to the owner's phone (ntfy). Runs every tick, so alerts arrive within a minute
    or so; a failure is recorded on the settings and never stops the rest of the tick."""
    from .notifications import check_and_send

    try:
        with owner_session(ctx.sessions, owner_id) as db:
            return check_and_send(ctx, db, owner_id)
    except Exception:
        log.exception("phone alerts failed", extra={"event": "phone_alerts_error"})
        return 0


def tick(ctx: AppContext, budget_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    planned = plan(ctx)
    remaining = max(budget_seconds - (time.monotonic() - started), 1)
    processed = run_until(ctx, remaining)
    return {"planned": planned, "processed": processed, "at": datetime.now(UTC).isoformat()}


@handler(MAINTENANCE_JOB)
def maintenance(ctx: AppContext, job: ClaimedJob, deadline: float) -> None:
    from .analytics_jobs import refresh_anomalies, refresh_recurring, snapshot_net_worth
    from .imports import purge_expired_files

    with lease_tx(ctx, job) as (db, row):
        user = db.get(User, job.owner_id)
        tz = user.timezone if user else ctx.settings.default_timezone
        today = today_in(tz)
        purged = purge_expired_files(db, job.owner_id)
        purge_old(db)
        n_rec = refresh_recurring(db, job.owner_id, today)
        n_anom = refresh_anomalies(db, job.owner_id, today)
        snapshot_net_worth(db, job.owner_id, today)
        finish(row, {"purged_files": purged, "recurring": n_rec, "anomalies": n_anom})
