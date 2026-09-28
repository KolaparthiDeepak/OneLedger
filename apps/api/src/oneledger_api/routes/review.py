"""Review inbox and resolution actions: transfers, duplicates, refunds, reversals, merges."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from oneledger_db.models import ReviewItem, Transfer, TransferLeg
from oneledger_domain.enums import ReviewKind, ReviewStatus, TransferStatus
from oneledger_shared.errors import NotFoundError, ValidationFailed
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import ReadAuth, WriteAuth
from ..services.categories import load_context
from ..services.relations import (
    dismiss_duplicate_review,
    link_refund,
    link_reversal,
    merge_duplicates,
    undo_merge,
)
from ..services.transfers import confirm_pair, mark_external, reject, run_matching
from ..services.txn_query import serialize

router = APIRouter(tags=["review"])


@router.get("/review")
def list_review(
    a: ReadAuth, status: ReviewStatus = ReviewStatus.OPEN, kind: ReviewKind | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    stmt = select(ReviewItem).where(ReviewItem.owner_id == a.owner_id, ReviewItem.status == status)
    if kind:
        stmt = stmt.where(ReviewItem.kind == kind)
    items = a.db.scalars(stmt.order_by(ReviewItem.created_at.desc()).limit(min(limit, 500))).all()
    cat = load_context(a.db, a.owner_id)
    txn_ids = {tid for i in items for tid in i.transaction_ids if tid}
    from oneledger_db.models import Transaction

    txns = {t.id: t for t in a.db.scalars(select(Transaction).where(Transaction.id.in_(txn_ids)))} if txn_ids else {}
    ser = {row["id"]: row for row in serialize(a.db, a.owner_id, list(txns.values()), cat, detail=True)}
    return [
        {
            "id": str(i.id),
            "kind": i.kind.value,
            "status": i.status.value,
            "summary": i.summary,
            "related": i.related,
            "evidence": i.evidence,
            "resolution": i.resolution,
            "created_at": i.created_at,
            "transactions": [ser[str(t)] for t in i.transaction_ids if t and str(t) in ser],
        }
        for i in items
    ]


def _review(a: WriteAuth, review_id: uuid.UUID) -> ReviewItem:
    item = a.db.get(ReviewItem, review_id)
    if item is None or item.owner_id != a.owner_id:
        raise NotFoundError()
    return item


@router.post("/transfers/{transfer_id}/confirm")
def confirm_transfer(transfer_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    t = a.db.scalars(
        select(Transfer).where(Transfer.id == transfer_id, Transfer.owner_id == a.owner_id).with_for_update()
    ).first()
    if t is None:
        raise NotFoundError()
    if t.status == TransferStatus.CONFIRMED:
        return {"status": t.status.value}
    legs = list(a.db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == t.id)))
    out = next(leg for leg in legs if leg.amount < 0)
    inn = next(leg for leg in legs if leg.amount > 0)
    cat = load_context(a.db, a.owner_id)
    confirm_pair(
        a.db,
        a.owner_id,
        out.allocation_id,
        inn.allocation_id,
        cat,
        actor=a.actor,
        method="MANUAL",
        reason="user_confirmed",
        transfer=t,
    )
    a.commit()
    return {"status": "CONFIRMED"}


@router.post("/transfers/{transfer_id}/reject")
def reject_transfer(transfer_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    cat = load_context(a.db, a.owner_id)
    t = reject(a.db, a.owner_id, transfer_id, cat, a.actor)
    a.commit()
    return {"status": t.status.value}


class RematchIn(BaseModel):
    start_date: str
    end_date_exclusive: str


@router.post("/transfers/rematch")
def rematch(body: RematchIn, a: WriteAuth) -> dict[str, int]:
    from datetime import date

    try:
        start, end = date.fromisoformat(body.start_date), date.fromisoformat(body.end_date_exclusive)
    except ValueError as exc:
        raise ValidationFailed("Invalid date.", code="INVALID_DATE") from exc
    if (end - start).days > 400 or end <= start:
        raise ValidationFailed("Choose a range of at most 400 days.", code="INVALID_RANGE")
    cat = load_context(a.db, a.owner_id)
    stats = run_matching(a.db, a.owner_id, cat, start=start, end_exclusive=end, actor=a.actor)
    a.commit()
    return stats


@router.post("/review/{review_id}/external")
def confirm_external(review_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    mark_external(a.db, a.owner_id, review_id, a.actor)
    a.commit()
    return {"status": "RESOLVED"}


@router.post("/review/{review_id}/not-duplicate")
def not_duplicate(review_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    dismiss_duplicate_review(a.db, a.owner_id, review_id, a.actor)
    a.commit()
    return {"status": "RESOLVED"}


@router.post("/review/{review_id}/dismiss")
def dismiss(review_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    from datetime import UTC, datetime

    from ..services.audit import audit

    item = _review(a, review_id)
    if item.kind not in (ReviewKind.RECONCILIATION, ReviewKind.SOURCE_REVISION, ReviewKind.CATEGORIZATION):
        raise ValidationFailed("Use the specific resolution action for this item.", code="USE_SPECIFIC_ACTION")
    item.status = ReviewStatus.DISMISSED
    item.resolution = "dismissed"
    item.resolved_at = datetime.now(UTC)
    audit(a.db, a.owner_id, a.actor, "review.dismiss", "review_item", item.id, ["status"])
    a.commit()
    return {"status": "DISMISSED"}


class MergeIn(BaseModel):
    survivor_id: uuid.UUID
    duplicate_id: uuid.UUID


@router.post("/transactions/merge")
def merge(body: MergeIn, a: WriteAuth) -> dict[str, str]:
    rel = merge_duplicates(a.db, a.owner_id, body.survivor_id, body.duplicate_id, a.actor)
    a.commit()
    return {"relation_id": str(rel.id)}


@router.post("/relations/{relation_id}/undo")
def undo(relation_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    undo_merge(a.db, a.owner_id, relation_id, a.actor)
    a.commit()
    return {"status": "undone"}


class LinkIn(BaseModel):
    original_id: uuid.UUID
    related_id: uuid.UUID


@router.post("/transactions/link-refund")
def refund(body: LinkIn, a: WriteAuth) -> dict[str, str]:
    rel = link_refund(a.db, a.owner_id, body.original_id, body.related_id, a.actor)
    a.commit()
    return {"relation_id": str(rel.id)}


@router.post("/transactions/link-reversal")
def reversal(body: LinkIn, a: WriteAuth) -> dict[str, str]:
    rel = link_reversal(a.db, a.owner_id, body.original_id, body.related_id, a.actor)
    a.commit()
    return {"relation_id": str(rel.id)}


@router.get("/transfers")
def list_transfers(
    a: ReadAuth, status: TransferStatus = TransferStatus.CONFIRMED, limit: int = 100
) -> list[dict[str, Any]]:
    rows = a.db.scalars(
        select(Transfer)
        .where(Transfer.owner_id == a.owner_id, Transfer.status == status)
        .order_by(Transfer.created_at.desc())
        .limit(min(limit, 500))
    ).all()
    out = []
    for t in rows:
        legs = list(a.db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == t.id)))
        out.append(
            {
                "id": str(t.id),
                "status": t.status.value,
                "method": t.method,
                "reason": t.reason,
                "confidence": str(t.confidence) if t.confidence is not None else None,
                "legs": [{"transaction_id": str(leg.transaction_id), "amount": str(leg.amount)} for leg in legs],
                "created_at": t.created_at,
            }
        )
    return out
