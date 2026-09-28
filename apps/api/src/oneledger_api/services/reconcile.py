"""Balance reconciliation. Discrepancies create review items; balancing entries are never inserted."""

from __future__ import annotations

import uuid
from decimal import Decimal

from oneledger_db.models import Account, BalanceSnapshot, Transaction
from oneledger_domain.enums import BalanceKind, ReviewKind
from oneledger_domain.networth import reconcile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .reports import active_txn_filter
from .review import open_review


def reconcile_account(db: Session, owner_id: uuid.UUID, account: Account, snap: BalanceSnapshot) -> dict[str, object]:
    kinds = [BalanceKind.CURRENT, BalanceKind.OPENING]
    prev = db.scalars(
        select(BalanceSnapshot)
        .where(
            BalanceSnapshot.owner_id == owner_id,
            BalanceSnapshot.account_id == account.id,
            BalanceSnapshot.as_of < snap.as_of,
            BalanceSnapshot.deleted_at.is_(None),
            BalanceSnapshot.balance_kind.in_(kinds),
            BalanceSnapshot.is_observed.is_(True),
        )
        .order_by(BalanceSnapshot.as_of.desc(), BalanceSnapshot.created_at.desc())
    ).first()
    if prev is None or snap.balance_kind not in kinds:
        snap.reconciliation_state = "NO_BASELINE"
        return {"state": "NO_BASELINE"}
    total = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.owner_id == owner_id,
            Transaction.account_id == account.id,
            active_txn_filter(),
            Transaction.transaction_date > prev.as_of,
            Transaction.transaction_date <= snap.as_of,
        )
    )
    result = reconcile(account.kind.nature, prev.amount, [Decimal(total or 0)], snap.amount)
    if result.balanced:
        snap.reconciliation_state = "BALANCED"
        return {"state": "BALANCED", "expected": str(result.expected)}
    snap.reconciliation_state = "DISCREPANCY"
    open_review(
        db,
        owner_id,
        ReviewKind.RECONCILIATION,
        f"recon:{snap.id}",
        [],
        summary=f"Balance on {snap.as_of.isoformat()} does not match transactions since {prev.as_of.isoformat()}. "
        "Some transactions may be missing or duplicated.",
        related={"account_id": str(account.id), "snapshot_id": str(snap.id), "baseline_snapshot_id": str(prev.id)},
        evidence={
            "expected": str(result.expected),
            "observed": str(result.observed),
            "unexplained_delta": str(result.discrepancy),
        },
    )
    return {
        "state": "DISCREPANCY",
        "expected": str(result.expected),
        "observed": str(result.observed),
        "unexplained_delta": str(result.discrepancy),
    }
