"""F12/F13 durable jobs: crash-safe, idempotent, and free of financial payloads."""

from __future__ import annotations

import time
import uuid

from oneledger_api.services import jobs
from oneledger_db.models import Job
from oneledger_db.session import owner_session
from sqlalchemy import select, text

from .helpers import MAPPING, account, csv_bytes, summary, txns, upload


def _confirm_without_running(api, acct, rows):
    imp = upload(api, acct, csv_bytes(rows))
    if imp["state"] == "NEEDS_MAPPING":
        imp = api.ok(api.put(f"/imports/{imp['id']}/mapping", json=MAPPING))
    return api.ok(
        api.post(f"/imports/{imp['id']}/confirm", json={"preview_hash": imp["preview_hash"], "run_now": False}), 202
    )


def test_f12_unpublished_until_complete_and_crash_safe(api, ctx, monkeypatch):
    acct = account(api, "Bank")
    rows = [f"{d:02d}/08/2026,SHOP {d},,{d}.00,,1.00" for d in range(1, 21)]
    imp = _confirm_without_running(api, acct, rows)
    monkeypatch.setattr(ctx.settings, "job_batch_size", 5)
    claimed = jobs.claim(ctx, 1)
    assert len(claimed) == 1
    jobs.run_one(ctx, claimed[0], time.monotonic() + 30)  # commits one chunk of 5
    assert txns(api) == []  # nothing published yet
    assert summary(api, "2026-08-01", "2026-09-01")["net_expenses"] == "0"
    # Simulate a worker crash mid-chunk: lease expires, another worker reclaims.
    with owner_session(ctx.sessions, None) as db:
        db.execute(text("UPDATE oneledger.jobs SET status='RUNNING', lease_expires_at = now() - interval '1 minute'"))
    stale = jobs.ClaimedJob(claimed[0].id, claimed[0].owner_id, claimed[0].type, claimed[0].lease_generation, 1)
    jobs.run_until(ctx, 20)
    # The stale worker's lease is gone; it cannot write.
    try:
        with jobs.lease_tx(ctx, stale):
            raise AssertionError("expired lease must not be usable")
    except jobs.LeaseLost:
        pass
    items = txns(api)
    assert len(items) == 20
    assert api.ok(api.get(f"/imports/{imp['id']}"))["state"] == "COMPLETED"


def test_f13_duplicate_delivery_does_not_duplicate(api, ctx):
    acct = account(api, "Bank")
    _confirm_without_running(api, acct, ["02/08/2026,SHOP,,10.00,,1.00"])
    jobs.run_until(ctx, 10)
    with owner_session(ctx.sessions, None) as db:  # redeliver the finished job
        db.execute(text("UPDATE oneledger.jobs SET status='QUEUED'"))
    jobs.run_until(ctx, 10)
    assert len(txns(api)) == 1


def test_jobs_have_no_financial_payloads(api, ctx):
    acct = account(api, "Bank")
    _confirm_without_running(api, acct, ["02/08/2026,SECRET MERCHANT,,10.00,,1.00"])
    with owner_session(ctx.sessions, uuid.UUID(api.ok(api.get("/me"))["id"])) as db:
        for j in db.scalars(select(Job)):
            assert "SECRET" not in str(j.payload) and set(j.payload) <= {"import_id", "revision", "day"}
