"""Derived analytics refreshed after ledger changes: recurrence, anomalies, net-worth snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from oneledger_db.models import (
    Account,
    Anomaly,
    NetWorthSnapshot,
    RecurringTransaction,
    RecurringTransactionMember,
    Transaction,
    TransactionAllocation,
    TransactionCategory,
    Transfer,
    TransferLeg,
    User,
)
from oneledger_domain import anomalies as an
from oneledger_domain import recurrence as rc
from oneledger_domain.enums import AccountKind, AllocationEffect, TransferStatus
from oneledger_domain.networth import CALCULATION_VERSION as NW_VERSION
from oneledger_domain.periods import today_in
from sqlalchemy import case, delete, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from ..context import AppContext
from .audit import current_ledger_revision
from .jobs import ClaimedJob, enqueue, finish, handler, lease_tx
from .reports import active_txn_filter, net_worth_at

REFRESH_JOB = "analytics.refresh"


def schedule_refresh(db: Session, owner_id: uuid.UUID) -> uuid.UUID:
    rev = current_ledger_revision(db, owner_id)
    return enqueue(db, owner_id, REFRESH_JOB, {"revision": rev}, f"analytics:{rev}", delay_seconds=2)


def _primary_rows(
    db: Session, owner_id: uuid.UUID, since: date
) -> list[tuple[Transaction, TransactionAllocation, str | None]]:
    rows = db.execute(
        select(Transaction, TransactionAllocation, TransactionCategory.code)
        .join(TransactionAllocation, TransactionAllocation.transaction_id == Transaction.id)
        .outerjoin(TransactionCategory, TransactionCategory.id == TransactionAllocation.category_id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.transaction_date >= since,
            TransactionAllocation.position == 0,
        )
    ).all()
    return [(t, a, code) for t, a, code in rows]


def _transfer_counterparts(db: Session, alloc_ids: list[uuid.UUID]) -> dict[uuid.UUID, Account]:
    """For allocations that are one leg of a confirmed transfer: the account on the other side."""
    if not alloc_ids:
        return {}
    mine = aliased(TransferLeg)
    other = aliased(TransferLeg)
    rows = db.execute(
        select(mine.allocation_id, Account)
        .join(other, (other.transfer_id == mine.transfer_id) & (other.id != mine.id))
        .join(Transfer, Transfer.id == mine.transfer_id)
        .join(Transaction, Transaction.id == other.transaction_id)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            mine.allocation_id.in_(alloc_ids),
            mine.is_active.is_(True),
            other.is_active.is_(True),
            Transfer.status == TransferStatus.CONFIRMED,
        )
    ).all()
    return {aid: acct for aid, acct in rows}


def _label(t: Transaction, merchant_key: str, counterpart: Account | None) -> str:
    if counterpart is not None:
        if counterpart.kind == AccountKind.CREDIT_CARD:
            return f"Card bill: {counterpart.name}"
        if counterpart.kind == AccountKind.LOAN:
            return f"Loan payment: {counterpart.name}"
        return f"Transfer to {counterpart.name}"
    if t.merchant_name and t.merchant_name.upper() != t.merchant_name:
        return t.merchant_name  # a name someone (or a pattern) wrote in mixed case is kept as is
    return rc.display_label(merchant_key)


def refresh_recurring(db: Session, owner_id: uuid.UUID, today: date) -> int:
    rows = _primary_rows(db, owner_id, today - timedelta(days=430))
    legs = _transfer_counterparts(db, [a.id for _t, a, _c in rows])
    # A transfer has two legs; only the money-out side is a recurring obligation. The receiving side
    # (a card's "payment received", a savings account's incoming sweep) would list it twice.
    rows = [(t, a, c) for t, a, c in rows if not (a.id in legs and t.amount > 0)]
    occ = [
        rc.Occurrence(t.id, t.account_id, t.transaction_date, t.amount, t.raw_description, t.merchant_name, code)
        for t, _a, code in rows
    ]
    patterns = rc.detect_recurring(occ)
    by_txn = {t.id: (t, a) for t, a, _c in rows}
    seen: set[str] = set()
    for p in patterns:
        seen.add(p.key)
        last_t, last_a = by_txn[p.occurrences[-1]]
        label = _label(last_t, p.merchant_key, legs.get(last_a.id))
        values = dict(
            owner_id=owner_id,
            pattern_key=p.key,
            account_id=p.account_id,
            merchant_key=p.merchant_key,
            label=label,
            category_id=last_a.category_id,
            recurrence_type=p.recurrence_type,
            cadence=p.cadence,
            typical_amount=p.typical_amount,
            currency=last_t.currency,
            amount_variable=p.amount_variable,
            sample_count=len(p.occurrences),
            last_date=p.last_date,
            expected_next_date=p.expected_next_date,
            anchor_day=p.expected_next_date.day,
            confidence=p.confidence,
            detection_version=rc.DETECTION_VERSION,
        )
        base = insert(RecurringTransaction).values(id=uuid.uuid4(), **values)
        # Refresh detection fields but never overwrite the user's accept/dismiss decision, and keep the
        # name once the owner has confirmed (and possibly renamed) the item.
        stmt = base.on_conflict_do_update(
            index_elements=["owner_id", "pattern_key"],
            set_={k: base.excluded[k] for k in values if k not in ("owner_id", "pattern_key", "label")}
            | {
                "label": case(
                    (RecurringTransaction.state == "SUGGESTED", base.excluded.label), else_=RecurringTransaction.label
                ),
                "updated_at": datetime.now(UTC),
            },
        ).returning(RecurringTransaction.id)
        rid = db.execute(stmt).scalar_one()
        db.execute(delete(RecurringTransactionMember).where(RecurringTransactionMember.recurring_id == rid))
        db.execute(
            insert(RecurringTransactionMember),
            [
                {"id": uuid.uuid4(), "owner_id": owner_id, "recurring_id": rid, "transaction_id": tid}
                for tid in p.occurrences
            ],
        )
    # Suggestions that no longer match anything (e.g. the receiving leg of a transfer) are dropped;
    # items the owner confirmed or dismissed are kept.
    stale = select(RecurringTransaction.id).where(
        RecurringTransaction.owner_id == owner_id,
        RecurringTransaction.state == "SUGGESTED",
        RecurringTransaction.pattern_key.not_in(seen) if seen else true(),
    )
    db.execute(delete(RecurringTransactionMember).where(RecurringTransactionMember.recurring_id.in_(stale)))
    db.execute(delete(RecurringTransaction).where(RecurringTransaction.id.in_(stale)))
    return len(seen)


def refresh_anomalies(db: Session, owner_id: uuid.UUID, today: date) -> int:
    rows = _primary_rows(db, owner_id, today - timedelta(days=365))
    spends = [
        an.Spend(
            t.id,
            t.transaction_date,
            -a.amount,
            code,
            rc.merchant_key(t.raw_description, t.merchant_name),
            t.raw_description,
        )
        for t, a, code in rows
        if a.effect == AllocationEffect.EXPENSE and a.amount < 0
    ]
    recent = [s for s in spends if s.transaction_date >= today - timedelta(days=45)]
    found = an.detect_anomalies(spends, recent)
    created = 0
    for f in found:
        res = db.execute(
            insert(Anomaly)
            .values(
                id=uuid.uuid4(),
                owner_id=owner_id,
                rule=f.rule,
                rule_version=an.RULES_VERSION,
                transaction_id=f.transaction_id,
                reason=f.reason,
                evidence=f.evidence,
            )
            .on_conflict_do_nothing(index_elements=["owner_id", "rule", "transaction_id"])
            .returning(Anomaly.id)
        ).first()
        created += int(res is not None)
    return created


def snapshot_net_worth(db: Session, owner_id: uuid.UUID, today: date) -> None:
    result, comps = net_worth_at(db, owner_id, today)
    rev = current_ledger_revision(db, owner_id)
    db.execute(
        update(NetWorthSnapshot)
        .where(
            NetWorthSnapshot.owner_id == owner_id,
            NetWorthSnapshot.snapshot_date == today,
            NetWorthSnapshot.is_current.is_(True),
        )
        .values(is_current=False)
    )
    for cur, t in result.totals.items():
        db.add(
            NetWorthSnapshot(
                owner_id=owner_id,
                snapshot_date=today,
                currency=cur,
                assets=t.assets,
                liabilities=t.liabilities,
                net_worth=t.net_worth,
                components={
                    c.key: {
                        "value": str(c.observation.value) if c.observation else None,
                        "label": c.label,
                        "nature": c.nature.value,
                    }
                    for c in comps
                    if c.currency == cur
                },
                source_ids=result.source_ids,
                partial=result.partial,
                coverage={"missing": result.missing, "stale": result.stale},
                calculation_version=NW_VERSION,
                ledger_revision=rev,
            )
        )


@handler(REFRESH_JOB)
def refresh_job(ctx: AppContext, job: ClaimedJob, deadline: float) -> None:
    with lease_tx(ctx, job) as (db, row):
        user = db.get(User, job.owner_id)
        tz = user.timezone if user else ctx.settings.default_timezone
        today = today_in(tz)
        n_rec = refresh_recurring(db, job.owner_id, today)
        from .categories import load_context
        from .products import sync_sip_contributions

        sync_sip_contributions(db, job.owner_id, load_context(db, job.owner_id), actor="worker")
        n_anom = refresh_anomalies(db, job.owner_id, today)
        snapshot_net_worth(db, job.owner_id, today)
        finish(row, {"recurring": n_rec, "anomalies": n_anom})
