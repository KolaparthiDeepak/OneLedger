"""Refunds, reversals, duplicate merge and undo. Nothing here physically deletes a transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from oneledger_db.models import (
    ReviewItem,
    TransactionRelation,
    TransactionSource,
    TransactionTagLink,
    TransferLeg,
)
from oneledger_domain.enums import (
    AllocationEffect,
    ClassificationSource,
    RelationKind,
    ReviewKind,
    ReviewStatus,
)
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import audit, bump_ledger_revision
from .ledger import allocations_of, get_transaction
from .review import resolve_reviews_for


def link_refund(
    db: Session, owner_id: uuid.UUID, purchase_id: uuid.UUID, refund_id: uuid.UUID, actor: str
) -> TransactionRelation:
    """Refund keeps expense treatment (a positive expense allocation) and inherits the purchase category."""
    purchase = get_transaction(db, owner_id, purchase_id, lock=True)
    refund = get_transaction(db, owner_id, refund_id, lock=True)
    if not (purchase.amount < 0 < refund.amount):
        raise ValidationFailed("A refund links a credit to an earlier debit.", code="REFUND_DIRECTION")
    if purchase.currency != refund.currency:
        raise ValidationFailed("Refund currency must match the purchase.", code="REFUND_CURRENCY")
    already = db.scalars(
        select(TransactionRelation).where(
            TransactionRelation.kind == RelationKind.REFUND,
            TransactionRelation.to_transaction_id == purchase.id,
            TransactionRelation.undone_at.is_(None),
        )
    ).all()
    refunded = sum((r.amount or Decimal(0) for r in already), Decimal(0))
    if refunded + refund.amount > -purchase.amount:
        raise ValidationFailed("Refunds would exceed the original purchase amount.", code="REFUND_EXCEEDS_PURCHASE")
    p_allocs = allocations_of(db, purchase.id)
    r_allocs = allocations_of(db, refund.id, lock=True)
    if len(r_allocs) == 1 and not r_allocs[0].is_locked:
        src = p_allocs[0]
        r_allocs[0].effect = AllocationEffect.EXPENSE if src.effect == AllocationEffect.EXPENSE else src.effect
        r_allocs[0].category_id = src.category_id
        r_allocs[0].classification_source = ClassificationSource.USER
        r_allocs[0].related_transaction_id = purchase.id
        r_allocs[0].is_locked = True
    rel = TransactionRelation(
        owner_id=owner_id,
        kind=RelationKind.REFUND,
        from_transaction_id=refund.id,
        to_transaction_id=purchase.id,
        amount=refund.amount,
        reason="user_linked",
    )
    db.add(rel)
    db.flush()
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "relation.refund", "transaction", refund.id, ["related_transaction_id"])
    return rel


def link_reversal(
    db: Session, owner_id: uuid.UUID, original_id: uuid.UUID, reversal_id: uuid.UUID, actor: str
) -> TransactionRelation:
    """Both legs are preserved and classified identically so their combined effect nets to zero."""
    original = get_transaction(db, owner_id, original_id, lock=True)
    reversal = get_transaction(db, owner_id, reversal_id, lock=True)
    if original.account_id != reversal.account_id or original.amount != -reversal.amount:
        raise ValidationFailed(
            "A reversal must be the exact opposite amount on the same account.", code="REVERSAL_MISMATCH"
        )
    o_allocs = allocations_of(db, original.id)
    r_allocs = allocations_of(db, reversal.id, lock=True)
    if len(o_allocs) != 1 or len(r_allocs) != 1:
        raise ConflictError("Split transactions cannot be linked as reversals automatically.", code="SPLIT_EXISTS")
    r_allocs[0].effect = o_allocs[0].effect
    r_allocs[0].category_id = o_allocs[0].category_id
    r_allocs[0].classification_source = ClassificationSource.USER
    r_allocs[0].related_transaction_id = original.id
    r_allocs[0].is_locked = True
    rel = TransactionRelation(
        owner_id=owner_id,
        kind=RelationKind.REVERSAL,
        from_transaction_id=reversal.id,
        to_transaction_id=original.id,
        amount=reversal.amount,
        reason="user_linked",
    )
    db.add(rel)
    db.flush()
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "relation.reversal", "transaction", reversal.id, ["related_transaction_id"])
    return rel


def merge_duplicates(
    db: Session, owner_id: uuid.UUID, survivor_id: uuid.UUID, duplicate_id: uuid.UUID, actor: str
) -> TransactionRelation:
    if survivor_id == duplicate_id:
        raise ValidationFailed("Choose two different transactions.", code="MERGE_SAME")
    first, second = sorted([survivor_id, duplicate_id])
    get_transaction(db, owner_id, first, lock=True)
    get_transaction(db, owner_id, second, lock=True)
    survivor = get_transaction(db, owner_id, survivor_id)
    dup = get_transaction(db, owner_id, duplicate_id)
    if survivor.account_id != dup.account_id or survivor.currency != dup.currency:
        raise ValidationFailed("Only transactions on the same account can be merged.", code="MERGE_ACCOUNT_MISMATCH")
    if survivor.amount != dup.amount:
        raise ValidationFailed("Duplicates must have the same amount.", code="MERGE_AMOUNT_MISMATCH")
    if dup.merged_into_id or survivor.merged_into_id or dup.deleted_at or survivor.deleted_at:
        raise ConflictError("One of these transactions is already merged or deleted.", code="TRANSACTION_INACTIVE")
    dup_alloc_ids = [a.id for a in allocations_of(db, dup.id)]
    if (
        dup_alloc_ids
        and db.scalars(
            select(TransferLeg.id).where(TransferLeg.allocation_id.in_(dup_alloc_ids), TransferLeg.is_active.is_(True))
        ).first()
    ):
        raise ConflictError(
            "Unlink the duplicate's confirmed transfer first, or keep it as the survivor.", code="TRANSFER_LINKED"
        )
    moved_sources = []
    for src in db.scalars(select(TransactionSource).where(TransactionSource.transaction_id == dup.id)):
        src.transaction_id = survivor.id
        moved_sources.append(str(src.id))
    survivor_tags = set(
        db.scalars(select(TransactionTagLink.tag_id).where(TransactionTagLink.transaction_id == survivor.id))
    )
    added_tags = []
    for link in db.scalars(select(TransactionTagLink).where(TransactionTagLink.transaction_id == dup.id)):
        if link.tag_id not in survivor_tags:
            db.add(TransactionTagLink(owner_id=owner_id, transaction_id=survivor.id, tag_id=link.tag_id))
            added_tags.append(str(link.tag_id))
    dup.merged_into_id = survivor.id
    dup.version += 1
    survivor.version += 1
    rel = TransactionRelation(
        owner_id=owner_id,
        kind=RelationKind.MERGE,
        from_transaction_id=dup.id,
        to_transaction_id=survivor.id,
        amount=dup.amount,
        reason="user_merged",
        state={"moved_sources": moved_sources, "added_tags": added_tags},
    )
    db.add(rel)
    db.flush()
    resolve_reviews_for(db, owner_id, [dup.id, survivor.id], kinds=[ReviewKind.POSSIBLE_DUPLICATE], resolution="merged")
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transaction.merge", "transaction", dup.id, ["merged_into_id"])
    return rel


def undo_merge(db: Session, owner_id: uuid.UUID, relation_id: uuid.UUID, actor: str) -> None:
    rel = db.scalars(
        select(TransactionRelation)
        .where(TransactionRelation.id == relation_id, TransactionRelation.owner_id == owner_id)
        .with_for_update()
    ).first()
    if rel is None or rel.kind != RelationKind.MERGE:
        raise NotFoundError()
    if rel.undone_at is not None:
        raise ConflictError("This merge was already undone.", code="ALREADY_UNDONE")
    dup = get_transaction(db, owner_id, rel.from_transaction_id, lock=True)
    for sid in rel.state.get("moved_sources", []):
        src = db.get(TransactionSource, uuid.UUID(sid))
        if src is not None:
            src.transaction_id = dup.id
    for tid in rel.state.get("added_tags", []):
        link = db.scalars(
            select(TransactionTagLink).where(
                TransactionTagLink.transaction_id == rel.to_transaction_id, TransactionTagLink.tag_id == uuid.UUID(tid)
            )
        ).first()
        if link is not None:
            db.delete(link)
    dup.merged_into_id = None
    dup.version += 1
    rel.undone_at = datetime.now(UTC)
    db.flush()
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transaction.unmerge", "transaction", dup.id, ["merged_into_id"])


def dismiss_duplicate_review(db: Session, owner_id: uuid.UUID, review_id: uuid.UUID, actor: str) -> None:
    """User confirms two similar transactions are both genuine."""
    item = db.get(ReviewItem, review_id)
    if item is None or item.owner_id != owner_id:
        raise NotFoundError()
    item.status = ReviewStatus.RESOLVED
    item.resolution = "not_duplicate"
    item.resolved_at = datetime.now(UTC)
    audit(db, owner_id, actor, "review.resolve", "review_item", item.id, ["status"])
