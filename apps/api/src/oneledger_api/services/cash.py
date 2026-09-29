"""ATM withdrawals move money into your cash wallet; they are not spending (review finding F3).

When the owner keeps a cash wallet, each ATM withdrawal from a bank account becomes a transfer into
it. The wallet side is an existing matching credit if the owner already entered one, otherwise a
derived movement ("Cash from ATM"). Spending is then what the owner records from the wallet.
Without a cash wallet, withdrawals stay in the "Cash" spending category, as before.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from oneledger_db.models import Account, Transaction, TransactionAllocation
from oneledger_domain.enums import (
    AccountKind,
    AccountStatus,
    ClassificationSource,
    PaymentChannel,
    TransactionSourceKind,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .categories import CategorizationContext
from .ledger import NewTransaction, active_leg_allocation_ids, allocations_of, create_transaction
from .reports import active_txn_filter
from .transfers import confirm_pair

CASH_MATCH_DAYS = 1


def primary_cash_wallet(db: Session, owner_id: uuid.UUID) -> Account | None:
    """The owner's cash wallet: the first active CASH account they created."""
    return db.scalars(
        select(Account)
        .where(
            Account.owner_id == owner_id,
            Account.kind == AccountKind.CASH,
            Account.status == AccountStatus.ACTIVE,
            Account.deleted_at.is_(None),
        )
        .order_by(Account.created_at, Account.id)
    ).first()


def _free_single_allocation(db: Session, txn: Transaction) -> TransactionAllocation | None:
    allocs = allocations_of(db, txn.id)
    if len(allocs) != 1 or active_leg_allocation_ids(db, [allocs[0].id]):
        return None
    return allocs[0]


def match_cash_withdrawals(
    db: Session,
    owner_id: uuid.UUID,
    cat: CategorizationContext,
    *,
    actor: str,
    start: date | None = None,
    end_exclusive: date | None = None,
) -> int:
    """Link ATM withdrawals on bank accounts to the cash wallet. Returns how many were linked.

    Skipped: withdrawals before the wallet's opening balance date (already included in it), ones the
    owner categorised as something other than cash, and ones already part of a transfer.
    """
    wallet = primary_cash_wallet(db, owner_id)
    if wallet is None:
        return 0
    cats = cat.categories_by_id
    q = (
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.amount < 0,
            Transaction.payment_channel == PaymentChannel.ATM,
            Transaction.currency == wallet.currency,
            Account.kind.in_([AccountKind.BANK_SAVINGS, AccountKind.BANK_CURRENT]),
        )
        .order_by(Transaction.transaction_date, Transaction.id)
    )
    if wallet.opening_date is not None:
        q = q.where(Transaction.transaction_date > wallet.opening_date)
    if start is not None:
        q = q.where(Transaction.transaction_date >= start)
    if end_exclusive is not None:
        q = q.where(Transaction.transaction_date < end_exclusive)
    linked = 0
    for txn in db.scalars(q).all():
        out_a = _free_single_allocation(db, txn)
        if out_a is None:
            continue
        code = cats[out_a.category_id].code if out_a.category_id in cats else ""
        if out_a.is_locked and out_a.classification_source == ClassificationSource.USER and code != "CASH":
            continue  # the owner said this withdrawal was something else
        amount = -txn.amount
        # Prefer a credit the owner already entered in the wallet for this withdrawal.
        existing = [
            t
            for t in db.scalars(
                select(Transaction).where(
                    Transaction.owner_id == owner_id,
                    Transaction.account_id == wallet.id,
                    active_txn_filter(),
                    Transaction.amount == amount,
                    Transaction.transaction_date >= txn.transaction_date - timedelta(days=CASH_MATCH_DAYS),
                    Transaction.transaction_date <= txn.transaction_date + timedelta(days=CASH_MATCH_DAYS),
                )
            )
            if _free_single_allocation(db, t) is not None
        ]
        if existing:
            in_a = _free_single_allocation(db, existing[0])
        else:
            derived = create_transaction(
                db,
                owner_id,
                NewTransaction(
                    account=wallet,
                    amount=amount,
                    transaction_date=txn.transaction_date,
                    description="Cash from ATM",
                    source=TransactionSourceKind.MANUAL_DERIVED,
                    provider_namespace="derived",
                ),
                cat,
                actor,
            )
            in_a = allocations_of(db, derived.id)[0]
        assert in_a is not None
        confirm_pair(
            db,
            owner_id,
            out_a.id,
            in_a.id,
            cat,
            actor=actor,
            method="AUTO",
            reason="cash_withdrawal",
            evidence={"wallet_id": str(wallet.id)},
        )
        linked += 1
    return linked
