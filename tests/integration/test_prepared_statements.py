"""Statements must keep working after psycopg prepares them (it does so after 5 executions)."""

from __future__ import annotations

import uuid

from oneledger_api.services.review import open_review
from oneledger_db.session import owner_session
from oneledger_domain.enums import ReviewKind

from tests.conftest import make_owner


def test_open_review_upsert_survives_prepared_plans(ctx):
    owner = make_owner(ctx)
    with owner_session(ctx.sessions, owner) as db:
        created = [
            open_review(db, owner, ReviewKind.UNMATCHED_TRANSFER, f"k:{i % 3}", [uuid.uuid4()], summary="x")
            for i in range(20)
        ]
    assert created.count(True) == 3
