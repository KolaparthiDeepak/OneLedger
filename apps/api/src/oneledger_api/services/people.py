"""Sharing expenses with other people, Splitwise style, inside your own ledger.

Each person has their own account (an asset): a positive balance is what they owe you, a negative
one is what you owe them. Nothing about them leaves your ledger.

* You paid for a group: the purchase is split; your part stays spending, each other person's part
  becomes a transfer into their account (so it is not your spending, and it is money owed to you).
* They paid for you: your share is spending recorded on their account (you now owe them).
* Settling up: money between your bank or cash and their account, as a transfer.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from oneledger_db.models import Account, BalanceSnapshot, Person, Transaction
from oneledger_domain.enums import (
    AccountKind,
    AllocationEffect,
    BalanceKind,
    BalanceSource,
    ClassificationSource,
    TransactionSourceKind,
)
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import audit
from .categories import CategorizationContext
from .ledger import (
    NewTransaction,
    SplitPart,
    active_leg_allocation_ids,
    allocations_of,
    create_transaction,
    get_account,
    get_transaction,
    split_transaction,
)
from .reports import account_balances, active_txn_filter
from .transfers import confirm_pair, reset_to_single_allocation

PERSON_OPENING_REF = "person_opening"
PERSON_OPENING_DATE = date(2000, 1, 1)
SHARED_CODE = "TRANSFERS_SHARED"


def get_person(db: Session, owner_id: uuid.UUID, person_id: uuid.UUID) -> Person:
    p = db.get(Person, person_id)
    if p is None or p.owner_id != owner_id:
        raise NotFoundError()
    return p


def create_person(
    db: Session, owner_id: uuid.UUID, *, name: str, email: str | None, currency: str, actor: str
) -> Person:
    name = name.strip()
    if not name:
        raise ValidationFailed("Enter a name.", code="NAME_REQUIRED")
    account = Account(
        owner_id=owner_id,
        name=f"{name} (shared)"[:120],
        kind=AccountKind.OTHER_ASSET,
        currency=currency,
        notes="Money this person owes you (positive) or you owe them (negative).",
    )
    db.add(account)
    db.flush()
    # A zero opening balance long ago: the balance is exactly the shares and settlements recorded.
    db.add(
        BalanceSnapshot(
            owner_id=owner_id,
            account_id=account.id,
            amount=Decimal(0),
            currency=currency,
            balance_kind=BalanceKind.OPENING,
            as_of=PERSON_OPENING_DATE,
            source=BalanceSource.MANUAL,
            is_observed=True,
            source_ref=PERSON_OPENING_REF,
        )
    )
    person = Person(owner_id=owner_id, name=name[:120], email=(email or None), account_id=account.id)
    db.add(person)
    db.flush()
    audit(db, owner_id, actor, "person.create", "person", person.id, ["name"])
    return person


def person_out(db: Session, owner_id: uuid.UUID, p: Person, today: date) -> dict[str, Any]:
    account = db.get(Account, p.account_id)
    assert account is not None
    cent = Decimal("0.01")
    bal = (account_balances(db, owner_id, today, [account])[0].balance or Decimal(0)).quantize(cent)
    last = db.scalar(
        select(Transaction.transaction_date)
        .where(Transaction.owner_id == owner_id, Transaction.account_id == account.id, active_txn_filter())
        .order_by(Transaction.transaction_date.desc())
    )
    return {
        "id": str(p.id),
        "name": p.name,
        "email": p.email,
        "account_id": str(p.account_id),
        "currency": account.currency,
        "balance": str(bal),
        "owes_you": str(bal) if bal > 0 else "0.00",
        "you_owe": str(-bal) if bal < 0 else "0.00",
        "last_activity": last,
        "archived": p.archived_at is not None,
    }


def _mark_shared(db: Session, cat: CategorizationContext, *alloc_ids: uuid.UUID) -> None:
    from oneledger_db.models import TransactionAllocation

    cid = cat.category_id(SHARED_CODE)
    for aid in alloc_ids:
        a = db.get(TransactionAllocation, aid)
        if a is not None and cid is not None:
            a.category_id = cid


def share_expense(
    db: Session,
    owner_id: uuid.UUID,
    txn_id: uuid.UUID,
    shares: list[tuple[uuid.UUID, Decimal]],
    cat: CategorizationContext,
    actor: str,
) -> list[dict[str, Any]]:
    """Split a payment you made so each person's share is owed to you instead of counted as spending."""
    txn = get_transaction(db, owner_id, txn_id, lock=True)
    if txn.amount >= 0:
        raise ValidationFailed("Only money you paid out can be shared.", code="SHARE_DIRECTION")
    allocs = allocations_of(db, txn.id, lock=True)
    if len(allocs) != 1 or active_leg_allocation_ids(db, [allocs[0].id]):
        raise ConflictError("Undo the split or transfer on this transaction first.", code="SPLIT_EXISTS")
    base = allocs[0]
    if base.effect not in (AllocationEffect.EXPENSE, AllocationEffect.UNCLASSIFIED):
        raise ValidationFailed("Only spending can be shared.", code="SHARE_NOT_EXPENSE")
    if not shares or len({pid for pid, _ in shares}) != len(shares):
        raise ValidationFailed("Choose each person once.", code="INVALID_SHARES")
    total_shared = sum((amt for _, amt in shares), Decimal(0))
    if any(amt <= 0 for _, amt in shares) or total_shared > -txn.amount:
        raise ValidationFailed("Shares must be positive and add up to no more than the payment.", code="INVALID_SHARES")
    people = [(get_person(db, owner_id, pid), amt) for pid, amt in shares]
    for person, _ in people:
        acct = get_account(db, owner_id, person.account_id)
        if acct.currency != txn.currency:
            raise ValidationFailed("Currency mismatch.", code="CURRENCY_MISMATCH")
    mine = txn.amount + total_shared
    parts: list[SplitPart] = []
    if mine != 0:
        effect = base.effect if base.effect == AllocationEffect.EXPENSE else AllocationEffect.EXPENSE
        parts.append(SplitPart(mine, effect, base.category_id, "Your share"))
    for person, amt in people:
        parts.append(SplitPart(-amt, AllocationEffect.TRANSFER, cat.category_id(SHARED_CODE), f"{person.name}'s share"))
    created = split_transaction(db, owner_id, txn, parts, actor, cat)
    theirs = [a for a in created if a.effect == AllocationEffect.TRANSFER]
    label = txn.merchant_name or txn.raw_description[:80]
    out = []
    for (person, amt), out_alloc in zip(people, theirs, strict=True):
        account = get_account(db, owner_id, person.account_id)
        derived = create_transaction(
            db,
            owner_id,
            NewTransaction(
                account=account,
                amount=amt,
                transaction_date=txn.transaction_date,
                description=f"{person.name}'s share: {label}"[:500],
                source=TransactionSourceKind.MANUAL_DERIVED,
                provider_namespace="derived",
            ),
            cat,
            actor,
        )
        in_alloc = allocations_of(db, derived.id)[0]
        confirm_pair(
            db,
            owner_id,
            out_alloc.id,
            in_alloc.id,
            cat,
            actor=actor,
            method="MANUAL",
            reason="shared_expense",
            evidence={"person_id": str(person.id)},
        )
        _mark_shared(db, cat, out_alloc.id, in_alloc.id)
        out.append({"person_id": str(person.id), "amount": str(amt)})
    audit(db, owner_id, actor, "transaction.share", "transaction", txn.id, ["allocations"])
    return out


def unshare_expense(
    db: Session, owner_id: uuid.UUID, txn_id: uuid.UUID, cat: CategorizationContext, actor: str
) -> None:
    txn = get_transaction(db, owner_id, txn_id, lock=True)
    reset_to_single_allocation(db, owner_id, txn, cat, reason="share_removed")
    audit(db, owner_id, actor, "transaction.unshare", "transaction", txn.id, ["allocations"])


def they_paid(
    db: Session,
    owner_id: uuid.UUID,
    person: Person,
    *,
    amount: Decimal,
    on: date,
    description: str,
    category_id: uuid.UUID | None,
    cat: CategorizationContext,
    actor: str,
) -> Transaction:
    """They paid for something of yours: your share is spending, and you now owe them."""
    if amount <= 0:
        raise ValidationFailed("Enter your share as a positive amount.", code="INVALID_AMOUNT")
    account = get_account(db, owner_id, person.account_id)
    txn = create_transaction(
        db,
        owner_id,
        NewTransaction(
            account=account,
            amount=-amount,
            transaction_date=on,
            description=description,
            source=TransactionSourceKind.MANUAL,
        ),
        cat,
        actor,
    )
    a = allocations_of(db, txn.id)[0]
    a.effect = AllocationEffect.EXPENSE
    a.category_id = category_id if category_id in cat.categories_by_id else a.category_id
    a.classification_source = ClassificationSource.USER
    a.is_locked = True
    audit(db, owner_id, actor, "person.they_paid", "person", person.id, ["balance"])
    return txn


def settle(
    db: Session,
    owner_id: uuid.UUID,
    person: Person,
    *,
    account_id: uuid.UUID,
    amount: Decimal,
    on: date,
    transaction_id: uuid.UUID | None,
    cat: CategorizationContext,
    actor: str,
) -> None:
    """Record money changing hands. ``amount`` > 0: they paid you; < 0: you paid them.

    With ``transaction_id`` the payment already in your ledger (e.g. a UPI credit on your bank
    statement) is linked instead of adding a new one.
    """
    if amount == 0:
        raise ValidationFailed("Enter an amount.", code="INVALID_AMOUNT")
    mine = get_account(db, owner_id, account_id)
    theirs = get_account(db, owner_id, person.account_id)
    if mine.id == theirs.id or mine.currency != theirs.currency:
        raise ValidationFailed("Choose one of your bank, cash or wallet accounts.", code="INVALID_ACCOUNT")
    if transaction_id is not None:
        txn = get_transaction(db, owner_id, transaction_id, lock=True)
        if txn.account_id != mine.id or txn.amount != amount:
            raise ValidationFailed("That transaction does not match this amount and account.", code="MISMATCH")
    else:
        txn = create_transaction(
            db,
            owner_id,
            NewTransaction(
                account=mine,
                amount=amount,
                transaction_date=on,
                description=f"{'From' if amount > 0 else 'To'} {person.name}: settling up",
                source=TransactionSourceKind.MANUAL,
            ),
            cat,
            actor,
        )
    own = allocations_of(db, txn.id, lock=True)
    if len(own) != 1 or active_leg_allocation_ids(db, [own[0].id]):
        raise ConflictError("That transaction is already split or matched.", code="SPLIT_EXISTS")
    derived = create_transaction(
        db,
        owner_id,
        NewTransaction(
            account=theirs,
            amount=-amount,
            transaction_date=txn.transaction_date,
            description=f"Settled with {person.name}",
            source=TransactionSourceKind.MANUAL_DERIVED,
            provider_namespace="derived",
        ),
        cat,
        actor,
    )
    other = allocations_of(db, derived.id)[0]
    confirm_pair(
        db,
        owner_id,
        own[0].id,
        other.id,
        cat,
        actor=actor,
        method="MANUAL",
        reason="settlement",
        evidence={"person_id": str(person.id)},
    )
    _mark_shared(db, cat, own[0].id, other.id)
    audit(db, owner_id, actor, "person.settle", "person", person.id, ["balance"])
