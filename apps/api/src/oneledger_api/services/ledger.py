"""Canonical ledger operations: create, classify, split, correct, delete/restore.

Invariants enforced here (and by database constraints):
* every posted transaction has allocations whose amounts sum exactly to its amount;
* imported source facts are only changed through audited revisions;
* locked (user) classifications survive re-categorization and provider refreshes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from oneledger_categorization.rules import TxnView
from oneledger_db.models import (
    Account,
    Transaction,
    TransactionAllocation,
    TransactionMerchant,
    TransactionSource,
    TransferLeg,
)
from oneledger_domain.dedup import fingerprint
from oneledger_domain.enums import (
    AccountKind,
    AllocationEffect,
    ClassificationSource,
    PaymentChannel,
    TransactionSourceKind,
    TransactionStatus,
)
from oneledger_domain.money import MoneyParseError, validate_precision
from oneledger_domain.text import (
    clean_description,
    detect_channel,
    extract_reference,
    normalize_description,
    normalize_reference,
    upi_parts,
)
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import audit, bump_ledger_revision, record_revision
from .categories import CategorizationContext


@dataclass
class NewTransaction:
    account: Account
    amount: Decimal
    transaction_date: date
    description: str
    source: TransactionSourceKind
    value_date: date | None = None
    occurred_at: datetime | None = None
    status: TransactionStatus = TransactionStatus.POSTED
    reference: str | None = None
    balance_after: Decimal | None = None
    source_drcr: str | None = None
    channel: PaymentChannel | None = None
    import_id: uuid.UUID | None = None
    is_published: bool = True
    notes: str | None = None
    provider_namespace: str = "manual"
    source_transaction_id: str | None = None
    import_row_id: uuid.UUID | None = None


def get_account(db: Session, owner_id: uuid.UUID, account_id: uuid.UUID) -> Account:
    acct = db.get(Account, account_id)
    if acct is None or acct.owner_id != owner_id or acct.deleted_at is not None:
        raise NotFoundError()
    return acct


def get_transaction(db: Session, owner_id: uuid.UUID, txn_id: uuid.UUID, *, lock: bool = False) -> Transaction:
    stmt = select(Transaction).where(Transaction.id == txn_id, Transaction.owner_id == owner_id)
    if lock:
        stmt = stmt.with_for_update()
    txn = db.scalars(stmt).first()
    if txn is None:
        raise NotFoundError()
    return txn


def allocations_of(db: Session, txn_id: uuid.UUID, *, lock: bool = False) -> list[TransactionAllocation]:
    stmt = (
        select(TransactionAllocation)
        .where(TransactionAllocation.transaction_id == txn_id)
        .order_by(TransactionAllocation.position, TransactionAllocation.id)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt))


def active_leg_allocation_ids(db: Session, allocation_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not allocation_ids:
        return set()
    return set(
        db.scalars(
            select(TransferLeg.allocation_id).where(
                TransferLeg.allocation_id.in_(allocation_ids), TransferLeg.is_active.is_(True)
            )
        )
    )


def _view(txn: Transaction) -> TxnView:
    return TxnView(
        account_id=txn.account_id,
        amount=txn.amount,
        description=txn.raw_description,
        merchant=txn.merchant_name,
        channel=txn.payment_channel,
    )


def _apply_classification(
    cat: CategorizationContext, txn: Transaction, alloc: TransactionAllocation, account_kind: AccountKind | None = None
) -> None:
    c = cat.classify(_view(txn))
    if c.source == ClassificationSource.FALLBACK and alloc.classification_source == ClassificationSource.AI:
        return  # rules have nothing better; keep the AI's category (the user can still change it)
    effect = c.effect
    category_code = c.category_code
    # A credit on a card/loan account that no rule explains is a payment toward the liability.
    if effect == AllocationEffect.UNCLASSIFIED and account_kind in (AccountKind.CREDIT_CARD,) and txn.amount > 0:
        effect, category_code = AllocationEffect.TRANSFER, "TRANSFERS_CARD_PAYMENT"
    # Income patterns never apply to liability accounts (a card credit is not salary).
    if effect == AllocationEffect.INCOME and account_kind in (AccountKind.CREDIT_CARD, AccountKind.LOAN):
        effect, category_code = AllocationEffect.UNCLASSIFIED, None
    alloc.effect = effect
    alloc.category_id = cat.category_id(category_code)
    alloc.classification_source = c.source
    alloc.classification_confidence = c.confidence
    alloc.rule_id = c.rule_id
    alloc.rule_version = c.rule_version
    alloc.model = None
    alloc.model_version = None
    if c.merchant_name and not txn.merchant_name:
        txn.merchant_name = c.merchant_name
        txn.merchant_id = c.merchant_id
    elif not txn.merchant_name and txn.payment_channel == PaymentChannel.UPI:
        payee, _vpa, _note = upi_parts(txn.raw_description)
        if payee:
            txn.merchant_name = payee[:120]


def create_transaction(
    db: Session, owner_id: uuid.UUID, nt: NewTransaction, cat: CategorizationContext, actor: str, *, bump: bool = True
) -> Transaction:
    acct = nt.account
    if nt.amount == 0:
        raise ValidationFailed("Amount must be non-zero.", code="ZERO_AMOUNT")
    try:
        validate_precision(nt.amount, acct.currency)
    except MoneyParseError as exc:
        raise ValidationFailed(
            "Amount has too many decimal places for the currency.", code="INVALID_PRECISION"
        ) from exc
    desc = clean_description(nt.description)
    if not desc:
        raise ValidationFailed("Description is required.", code="MISSING_DESCRIPTION")
    reference = normalize_reference(nt.reference) or normalize_reference(extract_reference(desc))
    txn = Transaction(
        id=uuid.uuid4(),
        owner_id=owner_id,
        account_id=acct.id,
        amount=nt.amount,
        currency=acct.currency,
        transaction_date=nt.transaction_date,
        value_date=nt.value_date,
        occurred_at=nt.occurred_at,
        status=nt.status,
        payment_channel=nt.channel or detect_channel(desc),
        source_drcr=nt.source_drcr,
        raw_description=desc,
        normalized_description=normalize_description(desc),
        reference=reference,
        balance_after=nt.balance_after,
        source=nt.source,
        fingerprint=fingerprint(acct.id, nt.transaction_date, nt.amount, acct.currency, desc, reference),
        import_id=nt.import_id,
        is_published=nt.is_published,
        notes=nt.notes,
    )
    db.add(txn)
    alloc = TransactionAllocation(
        id=uuid.uuid4(),
        owner_id=owner_id,
        transaction_id=txn.id,
        amount=nt.amount,
        effect=AllocationEffect.UNCLASSIFIED,
        classification_source=ClassificationSource.FALLBACK,
        position=0,
    )
    _apply_classification(cat, txn, alloc, acct.kind)
    db.add(alloc)
    db.add(
        TransactionSource(
            owner_id=owner_id,
            transaction_id=txn.id,
            account_id=acct.id,
            provider_namespace=nt.provider_namespace,
            source_transaction_id=nt.source_transaction_id,
            import_row_id=nt.import_row_id,
        )
    )
    db.flush()
    if bump:
        bump_ledger_revision(db, owner_id)
    return txn


def recategorize(
    db: Session,
    owner_id: uuid.UUID,
    txns: list[Transaction],
    cat: CategorizationContext,
    accounts: dict[uuid.UUID, Account] | None = None,
) -> int:
    """Re-run deterministic categorization on unlocked, unsplit, unmatched allocations."""
    changed = 0
    accounts = accounts or {}
    for txn in txns:
        allocs = allocations_of(db, txn.id)
        if len(allocs) != 1 or allocs[0].is_locked:
            continue
        alloc = allocs[0]
        if active_leg_allocation_ids(db, [alloc.id]):
            continue
        before = (alloc.effect, alloc.category_id)
        acct = accounts.get(txn.account_id) or db.get(Account, txn.account_id)
        _apply_classification(cat, txn, alloc, acct.kind if acct else None)
        if (alloc.effect, alloc.category_id) != before:
            changed += 1
    if changed:
        bump_ledger_revision(db, owner_id)
    return changed


def set_classification(
    db: Session,
    owner_id: uuid.UUID,
    txn: Transaction,
    allocation_id: uuid.UUID | None,
    category_id: uuid.UUID | None,
    effect: AllocationEffect,
    actor: str,
    cat: CategorizationContext,
    merchant_name: str | None = None,
) -> TransactionAllocation:
    allocs = allocations_of(db, txn.id, lock=True)
    target = (
        next((a for a in allocs if a.id == allocation_id), None)
        if allocation_id
        else (allocs[0] if len(allocs) == 1 else None)
    )
    if target is None:
        raise ValidationFailed("Specify which split to categorize.", code="ALLOCATION_REQUIRED")
    if category_id is not None and category_id not in cat.categories_by_id:
        raise NotFoundError("Category not found.")
    if active_leg_allocation_ids(db, [target.id]) and effect not in (
        AllocationEffect.TRANSFER,
        AllocationEffect.LOAN_PRINCIPAL,
    ):
        raise ConflictError(
            "Unlink the confirmed transfer before changing this classification.", code="TRANSFER_LINKED"
        )
    changed = ["category_id", "effect"]
    target.category_id = category_id
    target.effect = effect
    target.classification_source = ClassificationSource.USER
    target.classification_confidence = Decimal("1")
    target.rule_id = None
    target.rule_version = None
    target.is_locked = True
    if merchant_name is not None:
        txn.merchant_name = merchant_name.strip()[:120] or None
        txn.merchant_id = None
        changed.append("merchant_name")
    txn.version += 1
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transaction.classify", "transaction", txn.id, changed)
    return target


def unlock_classification(
    db: Session, owner_id: uuid.UUID, txn: Transaction, cat: CategorizationContext, actor: str
) -> None:
    allocs = allocations_of(db, txn.id, lock=True)
    for a in allocs:
        a.is_locked = False
    txn.version += 1
    recategorize(db, owner_id, [txn], cat)
    audit(db, owner_id, actor, "transaction.unlock_classification", "transaction", txn.id, ["is_locked"])


@dataclass
class SplitPart:
    amount: Decimal
    effect: AllocationEffect
    category_id: uuid.UUID | None
    note: str | None = None
    loan_id: uuid.UUID | None = None
    investment_id: uuid.UUID | None = None


def split_transaction(
    db: Session,
    owner_id: uuid.UUID,
    txn: Transaction,
    parts: list[SplitPart],
    actor: str,
    cat: CategorizationContext,
    *,
    source: ClassificationSource = ClassificationSource.USER,
) -> list[TransactionAllocation]:
    """Replace a transaction's allocations. ``source`` is SYSTEM for OneLedger's own automatic splits
    (e.g. an EMI matched to a loan); those are still locked so rules never undo them."""
    if not 1 <= len(parts) <= 20:
        raise ValidationFailed("A split needs between 1 and 20 parts.", code="INVALID_SPLIT")
    for p in parts:
        if p.amount == 0:
            raise ValidationFailed("Split amounts must be non-zero.", code="INVALID_SPLIT")
        try:
            validate_precision(p.amount, txn.currency)
        except MoneyParseError as exc:
            raise ValidationFailed("Split amount precision is invalid.", code="INVALID_PRECISION") from exc
        if p.category_id is not None and p.category_id not in cat.categories_by_id:
            raise NotFoundError("Category not found.")
    if sum((p.amount for p in parts), Decimal(0)) != txn.amount:
        raise ValidationFailed(
            "Split amounts must add up exactly to the transaction amount.", code="SPLIT_SUM_MISMATCH"
        )
    existing = allocations_of(db, txn.id, lock=True)
    if active_leg_allocation_ids(db, [a.id for a in existing]):
        raise ConflictError("Unlink confirmed transfers before splitting this transaction.", code="TRANSFER_LINKED")
    for a in existing:
        db.delete(a)
    db.flush()
    created = []
    for i, p in enumerate(parts):
        a = TransactionAllocation(
            id=uuid.uuid4(),
            owner_id=owner_id,
            transaction_id=txn.id,
            amount=p.amount,
            effect=p.effect,
            category_id=p.category_id,
            classification_source=source,
            classification_confidence=Decimal("1"),
            is_locked=True,
            note=(p.note or None),
            position=i,
            loan_id=p.loan_id,
            investment_id=p.investment_id,
        )
        db.add(a)
        created.append(a)
    txn.version += 1
    db.flush()
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transaction.split", "transaction", txn.id, ["allocations"])
    return created


def snapshot(txn: Transaction) -> dict[str, Any]:
    return {
        "amount": str(txn.amount),
        "currency": txn.currency,
        "transaction_date": txn.transaction_date.isoformat(),
        "value_date": txn.value_date.isoformat() if txn.value_date else None,
        "description": txn.raw_description,
        "status": txn.status.value,
        "reference": txn.reference,
    }


def correct_transaction(
    db: Session,
    owner_id: uuid.UUID,
    txn: Transaction,
    *,
    amount: Decimal | None,
    transaction_date: date | None,
    description: str | None,
    reason: str,
    actor: str,
    cat: CategorizationContext,
) -> Transaction:
    """Audited correction of source facts. Raw evidence is untouched; a revision preserves before/after."""
    from .transfers import unlink_for_transactions

    if txn.deleted_at is not None or txn.merged_into_id is not None:
        raise ConflictError("Deleted or merged transactions cannot be corrected.", code="TRANSACTION_INACTIVE")
    before = snapshot(txn)
    allocs = allocations_of(db, txn.id, lock=True)
    unlink_for_transactions(db, owner_id, [txn.id], actor, reason="transaction_corrected")
    if amount is not None and amount != txn.amount:
        if amount == 0:
            raise ValidationFailed("Amount must be non-zero.", code="ZERO_AMOUNT")
        validate_precision(amount, txn.currency)
        if len(allocs) > 1:
            raise ConflictError("Re-split the transaction after changing a split amount.", code="SPLIT_EXISTS")
        txn.amount = amount
        allocs[0].amount = amount
    if transaction_date is not None:
        txn.transaction_date = transaction_date
    if description is not None:
        desc = clean_description(description)
        if not desc:
            raise ValidationFailed("Description is required.", code="MISSING_DESCRIPTION")
        txn.raw_description = desc
        txn.normalized_description = normalize_description(desc)
    txn.fingerprint = fingerprint(
        txn.account_id, txn.transaction_date, txn.amount, txn.currency, txn.raw_description, txn.reference
    )
    txn.revision += 1
    txn.version += 1
    after = snapshot(txn)
    rev_id = record_revision(db, owner_id, txn.id, txn.revision, before, after, reason or "correction", actor)
    db.flush()
    recategorize(db, owner_id, [txn], cat)
    bump_ledger_revision(db, owner_id)
    audit(
        db,
        owner_id,
        actor,
        "transaction.correct",
        "transaction",
        txn.id,
        [k for k in after if after[k] != before[k]],
        revision_id=rev_id,
    )
    return txn


def soft_delete(db: Session, owner_id: uuid.UUID, txn: Transaction, actor: str) -> None:
    from .transfers import unlink_for_transactions

    if txn.source not in (TransactionSourceKind.MANUAL, TransactionSourceKind.MANUAL_DERIVED):
        raise ConflictError(
            "Imported transactions are evidence; merge duplicates or correct them instead.",
            code="SOURCE_TRANSACTION_IMMUTABLE",
        )
    if txn.deleted_at is not None:
        return
    unlink_for_transactions(db, owner_id, [txn.id], actor, reason="transaction_deleted")
    txn.deleted_at = datetime.now(UTC)
    txn.version += 1
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transaction.delete", "transaction", txn.id, ["deleted_at"])


def restore(db: Session, owner_id: uuid.UUID, txn: Transaction, actor: str) -> None:
    if txn.deleted_at is None:
        return
    txn.deleted_at = None
    txn.version += 1
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "transaction.restore", "transaction", txn.id, ["deleted_at"])


def upsert_merchant(
    db: Session, owner_id: uuid.UUID, name: str, category_id: uuid.UUID | None, aliases: list[str] | None = None
) -> TransactionMerchant:
    norm = normalize_description(name)
    if not norm:
        raise ValidationFailed("Merchant name is required.", code="INVALID_MERCHANT")
    m = db.scalars(
        select(TransactionMerchant).where(
            TransactionMerchant.owner_id == owner_id, TransactionMerchant.normalized_name == norm
        )
    ).first()
    if m is None:
        m = TransactionMerchant(
            owner_id=owner_id,
            name=name.strip()[:120],
            normalized_name=norm,
            aliases=[a for a in (aliases or []) if a][:20],
            default_category_id=category_id,
        )
        db.add(m)
    else:
        m.default_category_id = category_id
        if aliases is not None:
            m.aliases = [a for a in aliases if a][:20]
    db.flush()
    return m
