"""Transfer matching persistence: automatic proposals, confirmation, rejection and unlinking."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    ReviewItem,
    Transaction,
    TransactionAllocation,
    Transfer,
    TransferLeg,
)
from oneledger_domain.enums import (
    AccountKind,
    AllocationEffect,
    ClassificationSource,
    ReviewKind,
    ReviewStatus,
    TransactionSourceKind,
    TransactionStatus,
    TransferStatus,
)
from oneledger_domain.transfers import ALGORITHM_VERSION, Leg, match_transfers
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from .audit import audit, bump_ledger_revision
from .categories import CategorizationContext
from .review import open_review, resolve_reviews_for


def mask_last4(masked: str | None) -> str | None:
    digits = "".join(ch for ch in (masked or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


ELIGIBLE_EFFECTS = (
    AllocationEffect.UNCLASSIFIED,
    AllocationEffect.EXPENSE,
    AllocationEffect.INCOME,
    AllocationEffect.TRANSFER,
    AllocationEffect.LOAN_PRINCIPAL,
    AllocationEffect.INVESTMENT,
)


def _transfer_category(cat: CategorizationContext, a: Account, b: Account) -> tuple[AllocationEffect, str]:
    kinds = {a.kind, b.kind}
    if AccountKind.LOAN in kinds:
        return AllocationEffect.LOAN_PRINCIPAL, "LOANS_PRINCIPAL"
    if AccountKind.CREDIT_CARD in kinds:
        return AllocationEffect.TRANSFER, "TRANSFERS_CARD_PAYMENT"
    if AccountKind.CASH in kinds and cat.category_id("TRANSFERS_CASH") is not None:
        return AllocationEffect.TRANSFER, "TRANSFERS_CASH"
    return AllocationEffect.TRANSFER, "TRANSFERS_SELF"


def _lock_allocations(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, TransactionAllocation]:
    rows = db.scalars(
        select(TransactionAllocation)
        .where(TransactionAllocation.id.in_(ids))
        .order_by(TransactionAllocation.id)
        .with_for_update()
    ).all()
    return {r.id: r for r in rows}


def confirm_pair(
    db: Session,
    owner_id: uuid.UUID,
    out_alloc_id: uuid.UUID,
    in_alloc_id: uuid.UUID,
    cat: CategorizationContext,
    *,
    actor: str,
    method: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
    confidence: Decimal | None = None,
    transfer: Transfer | None = None,
) -> Transfer:
    allocs = _lock_allocations(db, [out_alloc_id, in_alloc_id])
    if len(allocs) != 2:
        raise NotFoundError("Allocation not found.")
    out_a, in_a = allocs[out_alloc_id], allocs[in_alloc_id]
    if out_a.amount > 0 > in_a.amount:
        out_a, in_a = in_a, out_a
    if not (out_a.amount < 0 < in_a.amount) or out_a.amount != -in_a.amount:
        raise ValidationFailed("Transfer legs must be equal and opposite amounts.", code="TRANSFER_AMOUNT_MISMATCH")
    out_t = db.get(Transaction, out_a.transaction_id)
    in_t = db.get(Transaction, in_a.transaction_id)
    assert out_t and in_t
    if out_t.account_id == in_t.account_id:
        raise ValidationFailed("Transfer legs must be on different accounts.", code="TRANSFER_SAME_ACCOUNT")
    if out_t.currency != in_t.currency:
        raise ValidationFailed("Cross-currency transfers are not supported yet.", code="TRANSFER_CURRENCY_MISMATCH")
    for t in (out_t, in_t):
        if t.deleted_at or t.merged_into_id or t.status != TransactionStatus.POSTED:
            raise ConflictError("Only active posted transactions can be matched.", code="TRANSACTION_INACTIVE")
    busy = set(
        db.scalars(
            select(TransferLeg.allocation_id).where(
                TransferLeg.allocation_id.in_([out_a.id, in_a.id]), TransferLeg.is_active.is_(True)
            )
        )
    )
    if busy:
        raise ConflictError("One of these transactions is already matched.", code="ALREADY_MATCHED")
    out_acct, in_acct = db.get(Account, out_t.account_id), db.get(Account, in_t.account_id)
    assert out_acct and in_acct
    effect, code = _transfer_category(cat, out_acct, in_acct)
    previous = {
        str(a.id): {
            "effect": a.effect.value,
            "category_id": str(a.category_id) if a.category_id else None,
            "source": a.classification_source.value,
            "locked": a.is_locked,
        }
        for a in (out_a, in_a)
    }
    now = datetime.now(UTC)
    if transfer is None:
        transfer = Transfer(
            owner_id=owner_id,
            status=TransferStatus.CONFIRMED,
            method=method,
            reason=reason,
            evidence={**(evidence or {}), "previous": previous},
            confidence=confidence,
            algorithm_version=ALGORITHM_VERSION,
            confirmed_at=now,
            decided_by="user" if method == "MANUAL" else "system",
        )
        db.add(transfer)
        db.flush()
        for a, t in ((out_a, out_t), (in_a, in_t)):
            db.add(
                TransferLeg(
                    owner_id=owner_id,
                    transfer_id=transfer.id,
                    allocation_id=a.id,
                    transaction_id=t.id,
                    amount=a.amount,
                    is_active=True,
                )
            )
    else:
        transfer.status = TransferStatus.CONFIRMED
        transfer.confirmed_at = now
        transfer.decided_by = "user" if method == "MANUAL" else "system"
        transfer.evidence = {**transfer.evidence, "previous": previous}
        for leg in db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == transfer.id)):
            leg.is_active = True
    for a in (out_a, in_a):
        a.effect = effect
        a.category_id = cat.category_id(code)
        a.classification_source = (
            ClassificationSource.TRANSFER_MATCH if method != "MANUAL" else ClassificationSource.USER
        )
        a.classification_confidence = confidence if confidence is not None else Decimal("1")
    db.flush()
    resolve_reviews_for(
        db,
        owner_id,
        [out_t.id, in_t.id],
        kinds=[ReviewKind.TRANSFER_SUGGESTION, ReviewKind.UNMATCHED_TRANSFER],
        resolution="matched",
    )
    # Other suggestions involving these legs are now moot.
    for other in db.scalars(
        select(Transfer)
        .join(TransferLeg, TransferLeg.transfer_id == Transfer.id)
        .where(
            Transfer.status == TransferStatus.SUGGESTED,
            Transfer.id != transfer.id,
            TransferLeg.allocation_id.in_([out_a.id, in_a.id]),
        )
    ).unique():
        other.status = TransferStatus.REJECTED
        other.decided_by = "system"
        other.reason = "superseded"
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transfer.confirm", "transfer", transfer.id, ["status"])
    return transfer


def _restore_previous(db: Session, owner_id: uuid.UUID, transfer: Transfer, cat: CategorizationContext) -> None:
    from .ledger import recategorize

    previous = transfer.evidence.get("previous", {})
    legs = list(db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == transfer.id)))
    txns = []
    for leg in legs:
        a = db.get(TransactionAllocation, leg.allocation_id)
        if a is None:
            continue
        prev = previous.get(str(a.id))
        if prev and prev.get("locked"):
            a.effect = AllocationEffect(prev["effect"])
            a.category_id = uuid.UUID(prev["category_id"]) if prev["category_id"] else None
            a.classification_source = ClassificationSource(prev["source"])
            a.is_locked = True
        else:
            a.is_locked = False
            t = db.get(Transaction, a.transaction_id)
            if t:
                txns.append(t)
    db.flush()
    recategorize(db, owner_id, txns, cat)


def reject(
    db: Session, owner_id: uuid.UUID, transfer_id: uuid.UUID, cat: CategorizationContext, actor: str
) -> Transfer:
    transfer = db.scalars(
        select(Transfer).where(Transfer.id == transfer_id, Transfer.owner_id == owner_id).with_for_update()
    ).first()
    if transfer is None:
        raise NotFoundError()
    was_confirmed = transfer.status == TransferStatus.CONFIRMED
    for leg in db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == transfer.id)):
        leg.is_active = False
    transfer.status = TransferStatus.REJECTED
    transfer.decided_by = "user"
    db.flush()
    if was_confirmed:
        _restore_previous(db, owner_id, transfer, cat)
    txn_ids = list(db.scalars(select(TransferLeg.transaction_id).where(TransferLeg.transfer_id == transfer.id)))
    resolve_reviews_for(db, owner_id, txn_ids, kinds=[ReviewKind.TRANSFER_SUGGESTION], resolution="rejected")
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transfer.reject", "transfer", transfer.id, ["status"])
    return transfer


def unlink_for_transactions(db: Session, owner_id: uuid.UUID, txn_ids: list[uuid.UUID], actor: str, reason: str) -> int:
    """Invalidate matches touching edited/merged/deleted transactions; they return to suggestions."""
    from .categories import load_context

    transfers = list(
        db.scalars(
            select(Transfer)
            .join(TransferLeg, TransferLeg.transfer_id == Transfer.id)
            .where(TransferLeg.transaction_id.in_(txn_ids), Transfer.status == TransferStatus.CONFIRMED)
        ).unique()
    )
    if not transfers:
        return 0
    cat = load_context(db, owner_id)
    for t in transfers:
        for leg in db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == t.id)):
            leg.is_active = False
        t.status = TransferStatus.SUGGESTED
        t.reason = reason
        t.confirmed_at = None
        db.flush()
        _restore_previous(db, owner_id, t, cat)
        audit(db, owner_id, actor, "transfer.invalidate", "transfer", t.id, ["status"])
    return len(transfers)


def run_matching(
    db: Session,
    owner_id: uuid.UUID,
    cat: CategorizationContext,
    *,
    start: date,
    end_exclusive: date,
    actor: str = "system",
) -> dict[str, int]:
    """Propose/confirm transfers among eligible allocations in a date window (± gap days)."""
    lo, hi = start - timedelta(days=3), end_exclusive + timedelta(days=3)
    active_leg = select(TransferLeg.allocation_id).where(TransferLeg.is_active.is_(True))
    rows = db.execute(
        select(TransactionAllocation, Transaction, Account)
        .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            Transaction.owner_id == owner_id,
            Transaction.transaction_date >= lo,
            Transaction.transaction_date < hi,
            Transaction.status == TransactionStatus.POSTED,
            Transaction.deleted_at.is_(None),
            Transaction.merged_into_id.is_(None),
            Transaction.is_published.is_(True),
            TransactionAllocation.effect.in_(ELIGIBLE_EFFECTS),
            TransactionAllocation.id.not_in(active_leg),
            # A user's explicit non-transfer classification is respected.
            ~and_(
                TransactionAllocation.is_locked.is_(True),
                TransactionAllocation.effect.not_in([AllocationEffect.TRANSFER, AllocationEffect.LOAN_PRINCIPAL]),
            ),
        )
    ).all()
    legs: list[Leg] = []
    by_alloc: dict[uuid.UUID, tuple[TransactionAllocation, Transaction, Account]] = {}
    for alloc, txn, acct in rows:
        by_alloc[alloc.id] = (alloc, txn, acct)
        legs.append(
            Leg(
                alloc.id,
                txn.id,
                acct.id,
                acct.kind,
                mask_last4(acct.masked_identifier),
                alloc.amount,
                txn.currency,
                txn.transaction_date,
                txn.raw_description,
                txn.reference,
                txn.payment_channel,
            )
        )
    rejected = frozenset((a, b) for a, b in _rejected_pairs(db, owner_id, list(by_alloc)))
    proposals = match_transfers(legs, rejected)
    stats = {"auto_confirmed": 0, "suggested": 0, "unmatched_flagged": 0}
    matched: set[uuid.UUID] = set()
    for p in proposals:
        if p.outflow.allocation_id in matched or p.inflow.allocation_id in matched:
            continue
        ev = {k: v for k, v in p.evidence.items()}
        if p.auto_confirm:
            confirm_pair(
                db,
                owner_id,
                p.outflow.allocation_id,
                p.inflow.allocation_id,
                cat,
                actor=actor,
                method="AUTO",
                reason=p.reason,
                evidence=ev,
                confidence=p.confidence,
            )
            matched |= {p.outflow.allocation_id, p.inflow.allocation_id}
            stats["auto_confirmed"] += 1
            continue
        if _suggestion_exists(db, owner_id, p.outflow.allocation_id, p.inflow.allocation_id):
            continue
        t = Transfer(
            owner_id=owner_id,
            status=TransferStatus.SUGGESTED,
            method="AUTO",
            reason=p.reason,
            evidence=ev,
            confidence=p.confidence,
            algorithm_version=ALGORITHM_VERSION,
        )
        db.add(t)
        db.flush()
        for leg in (p.outflow, p.inflow):
            db.add(
                TransferLeg(
                    owner_id=owner_id,
                    transfer_id=t.id,
                    allocation_id=leg.allocation_id,
                    transaction_id=leg.transaction_id,
                    amount=leg.amount,
                    is_active=False,
                )
            )
        open_review(
            db,
            owner_id,
            ReviewKind.TRANSFER_SUGGESTION,
            f"transfer:{t.id}",
            [p.outflow.transaction_id, p.inflow.transaction_id],
            summary="Possible transfer between your accounts. Confirm to exclude it from income and expenses.",
            related={"transfer_id": str(t.id)},
            evidence={"confidence": str(p.confidence), "reason": p.reason},
        )
        stats["suggested"] += 1
    # Transfer-classified legs without any counterpart are incomplete coverage, not spending.
    proposed = {p.outflow.allocation_id for p in proposals} | {p.inflow.allocation_id for p in proposals}
    horizon = datetime.now(UTC).date() - timedelta(days=3)
    for alloc_id, (alloc, txn, _acct) in by_alloc.items():
        if (
            alloc.effect == AllocationEffect.TRANSFER
            and alloc_id not in proposed
            and alloc_id not in matched
            and txn.transaction_date <= horizon
        ) and open_review(
            db,
            owner_id,
            ReviewKind.UNMATCHED_TRANSFER,
            f"unmatched:{alloc_id}",
            [txn.id],
            summary="Transfer or card payment without a matching movement on another account. "
            "Add the other account or confirm it is external.",
            related={"allocation_id": str(alloc_id)},
        ):
            stats["unmatched_flagged"] += 1
    return stats


def _rejected_pairs(db: Session, owner_id: uuid.UUID, alloc_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, uuid.UUID]]:
    if not alloc_ids:
        return []
    rows = db.execute(
        select(TransferLeg.transfer_id, TransferLeg.allocation_id, TransferLeg.amount)
        .join(Transfer, Transfer.id == TransferLeg.transfer_id)
        .where(
            Transfer.owner_id == owner_id,
            Transfer.status == TransferStatus.REJECTED,
            Transfer.decided_by == "user",
            TransferLeg.allocation_id.in_(alloc_ids),
        )
    ).all()
    by_transfer: dict[uuid.UUID, dict[str, uuid.UUID]] = {}
    for tid, aid, amt in rows:
        by_transfer.setdefault(tid, {})["out" if amt < 0 else "in"] = aid
    return [(v["out"], v["in"]) for v in by_transfer.values() if "out" in v and "in" in v]


def _suggestion_exists(db: Session, owner_id: uuid.UUID, a: uuid.UUID, b: uuid.UUID) -> bool:
    legs = db.execute(
        select(TransferLeg.transfer_id, TransferLeg.allocation_id)
        .join(Transfer, Transfer.id == TransferLeg.transfer_id)
        .where(
            Transfer.owner_id == owner_id,
            Transfer.status.in_([TransferStatus.SUGGESTED, TransferStatus.REJECTED]),
            TransferLeg.allocation_id.in_([a, b]),
        )
    ).all()
    seen: dict[uuid.UUID, set[uuid.UUID]] = {}
    for tid, aid in legs:
        seen.setdefault(tid, set()).add(aid)
    return any(s == {a, b} for s in seen.values())


def mark_external(db: Session, owner_id: uuid.UUID, review_id: uuid.UUID, actor: str) -> None:
    """User states an unmatched transfer leg's counterpart is outside tracked accounts."""
    item = db.get(ReviewItem, review_id)
    if item is None or item.owner_id != owner_id or item.kind != ReviewKind.UNMATCHED_TRANSFER:
        raise NotFoundError()
    item.status = ReviewStatus.RESOLVED
    item.resolution = "external_confirmed"
    item.resolved_at = datetime.now(UTC)
    audit(db, owner_id, actor, "review.resolve", "review_item", item.id, ["status"])


def reset_to_single_allocation(
    db: Session, owner_id: uuid.UUID, txn: Transaction, cat: CategorizationContext, *, reason: str
) -> None:
    """Undo an automatic or derived split: void confirmed transfers on the transaction's allocations,
    soft-delete the derived movements on the other side, and leave one ordinary allocation that is
    re-categorised by the owner's rules."""
    from oneledger_db.models import TransactionSource

    from .ledger import allocations_of, recategorize

    now = datetime.now(UTC)
    allocs = allocations_of(db, txn.id, lock=True)
    transfers = list(
        db.scalars(
            select(Transfer)
            .join(TransferLeg, TransferLeg.transfer_id == Transfer.id)
            .where(
                TransferLeg.allocation_id.in_([a.id for a in allocs]),
                TransferLeg.is_active.is_(True),
                Transfer.status == TransferStatus.CONFIRMED,
            )
        ).unique()
    )
    for tr in transfers:
        legs = list(db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == tr.id)))
        for leg in legs:
            leg.is_active = False
        tr.status, tr.decided_by = TransferStatus.REJECTED, "system"
        tr.evidence = {**tr.evidence, "voided": reason}
        db.flush()
        _restore_previous(db, owner_id, tr, cat)
        for leg in legs:
            other = db.get(Transaction, leg.transaction_id)
            if other is not None and other.id != txn.id and other.source == TransactionSourceKind.MANUAL_DERIVED:
                other.deleted_at = now
                other.version += 1
                db.execute(
                    update(TransactionSource)
                    .where(TransactionSource.transaction_id == other.id)
                    .values(is_active=False)
                )
    for a in allocs:
        db.delete(a)
    db.flush()
    db.add(
        TransactionAllocation(
            id=uuid.uuid4(),
            owner_id=owner_id,
            transaction_id=txn.id,
            amount=txn.amount,
            effect=AllocationEffect.UNCLASSIFIED,
            classification_source=ClassificationSource.FALLBACK,
            position=0,
        )
    )
    txn.version += 1
    db.flush()
    recategorize(db, owner_id, [txn], cat)
    bump_ledger_revision(db, owner_id)
