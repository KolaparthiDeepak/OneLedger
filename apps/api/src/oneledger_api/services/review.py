"""Review inbox: duplicates, transfer suggestions, unmatched legs, reconciliation discrepancies."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from oneledger_db.models import ReviewItem
from oneledger_domain.enums import ReviewKind, ReviewStatus
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def open_review(
    db: Session,
    owner_id: uuid.UUID,
    kind: ReviewKind,
    dedupe_key: str,
    transaction_ids: list[uuid.UUID],
    *,
    summary: str,
    related: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> bool:
    """Create an open review item unless an open one with the same key exists. Returns True if created."""
    stmt = (
        insert(ReviewItem)
        .values(
            id=uuid.uuid4(),
            owner_id=owner_id,
            kind=kind,
            status=ReviewStatus.OPEN,
            dedupe_key=dedupe_key[:200],
            transaction_ids=transaction_ids,
            related=related or {},
            evidence=evidence or {},
            summary=summary[:300],
        )
        # Literal predicate: a bound parameter cannot be matched to the partial unique index once
        # psycopg switches the statement to a prepared (generic) plan.
        .on_conflict_do_nothing(index_elements=["owner_id", "dedupe_key"], index_where=text("status = 'OPEN'"))
        .returning(ReviewItem.id)
    )
    return db.execute(stmt).first() is not None


def resolve_reviews_for(
    db: Session, owner_id: uuid.UUID, txn_ids: list[uuid.UUID], *, kinds: list[ReviewKind], resolution: str
) -> int:
    if not txn_ids:
        return 0
    items = db.scalars(
        select(ReviewItem).where(
            ReviewItem.owner_id == owner_id,
            ReviewItem.status == ReviewStatus.OPEN,
            ReviewItem.kind.in_(kinds),
            ReviewItem.transaction_ids.overlap(txn_ids),
        )
    ).all()
    now = datetime.now(UTC)
    for item in items:
        item.status = ReviewStatus.RESOLVED
        item.resolution = resolution
        item.resolved_at = now
    return len(items)


def open_counts(db: Session, owner_id: uuid.UUID) -> dict[str, int]:
    from sqlalchemy import func

    rows = db.execute(
        select(ReviewItem.kind, func.count())
        .where(ReviewItem.owner_id == owner_id, ReviewItem.status == ReviewStatus.OPEN)
        .group_by(ReviewItem.kind)
    ).all()
    return {k.value: int(n) for k, n in rows}
