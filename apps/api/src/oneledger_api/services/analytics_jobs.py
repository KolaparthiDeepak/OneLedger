"""Derived analytics refreshed after ledger changes: recurrence, anomalies, net-worth snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from oneledger_db.models import (
    Anomaly,
    NetWorthSnapshot,
    RecurringTransaction,
    RecurringTransactionMember,
    Transaction,
    TransactionAllocation,
    TransactionCategory,
    User,
)
from oneledger_domain import anomalies as an
from oneledger_domain import recurrence as rc
from oneledger_domain.enums import AllocationEffect
from oneledger_domain.networth import CALCULATION_VERSION as NW_VERSION
from oneledger_domain.periods import today_in
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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


def refresh_recurring(db: Session, owner_id: uuid.UUID, today: date) -> int:
    rows = _primary_rows(db, owner_id, today - timedelta(days=430))
    occ = [
        rc.Occurrence(t.id, t.account_id, t.transaction_date, t.amount, t.raw_description, t.merchant_name, code)
        for t, _a, code in rows
    ]
    patterns = rc.detect_recurring(occ)
    currencies = {t.id: t.currency for t, _a, _c in rows}
    cat_of = {t.id: a.category_id for t, a, _c in rows}
    seen: set[str] = set()
    for p in patterns:
        seen.add(p.key)
        label = p.merchant_key.title()
        values = dict(
            owner_id=owner_id,
            pattern_key=p.key,
            account_id=p.account_id,
            merchant_key=p.merchant_key,
            label=label,
            category_id=cat_of.get(p.occurrences[-1]),
            recurrence_type=p.recurrence_type,
            cadence=p.cadence,
            typical_amount=p.typical_amount,
            currency=currencies[p.occurrences[-1]],
            amount_variable=p.amount_variable,
            sample_count=len(p.occurrences),
            last_date=p.last_date,
            expected_next_date=p.expected_next_date,
            anchor_day=p.expected_next_date.day,
            confidence=p.confidence,
            detection_version=rc.DETECTION_VERSION,
        )
        base = insert(RecurringTransaction).values(id=uuid.uuid4(), **values)
        # Refresh detection fields but never overwrite the user's accept/dismiss decision.
        stmt = base.on_conflict_do_update(
            index_elements=["owner_id", "pattern_key"],
            set_={k: base.excluded[k] for k in values if k not in ("owner_id", "pattern_key", "label")}
            | {"updated_at": datetime.now(UTC)},
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
        n_anom = refresh_anomalies(db, job.owner_id, today)
        snapshot_net_worth(db, job.owner_id, today)
        finish(row, {"recurring": n_rec, "anomalies": n_anom})
