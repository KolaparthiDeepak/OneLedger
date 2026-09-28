"""Recurring payments, budgets, goals and forecasts. None of these create money movements."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    Budget,
    FinancialGoal,
    GoalContribution,
    RecurringTransaction,
    Transaction,
    TransactionAllocation,
    User,
)
from oneledger_domain import forecast as fc
from oneledger_domain.enums import AllocationEffect, RecurrenceCadence
from oneledger_domain.periods import DateRange, month_range
from oneledger_domain.recurrence import is_overdue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .categories import load_context
from .reports import account_balances, active_txn_filter, record_query

MONTHLY_FACTOR = {
    RecurrenceCadence.WEEKLY: Decimal("4.345"),
    RecurrenceCadence.MONTHLY: Decimal(1),
    RecurrenceCadence.QUARTERLY: Decimal(1) / 3,
    RecurrenceCadence.ANNUAL: Decimal(1) / 12,
}


def recurring_out(r: RecurringTransaction, today: date, account_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "label": r.label,
        "account_id": str(r.account_id),
        "account_name": account_name,
        "type": r.recurrence_type.value,
        "cadence": r.cadence.value,
        "typical_amount": str(r.typical_amount),
        "currency": r.currency,
        "amount_variable": r.amount_variable,
        "occurrences": r.sample_count,
        "last_date": r.last_date,
        "expected_next_date": r.expected_next_date,
        "confidence": str(r.confidence),
        "state": r.state,
        "overdue": is_overdue(r.expected_next_date, r.cadence, today),
        "monthly_equivalent": str((r.typical_amount * MONTHLY_FACTOR[r.cadence]).quantize(Decimal("0.01"))),
        "category_id": str(r.category_id) if r.category_id else None,
    }


def list_recurring(
    db: Session, owner_id: uuid.UUID, today: date, include_dismissed: bool = False
) -> list[dict[str, Any]]:
    stmt = (
        select(RecurringTransaction, Account.name)
        .join(Account, Account.id == RecurringTransaction.account_id)
        .where(RecurringTransaction.owner_id == owner_id)
    )
    if not include_dismissed:
        stmt = stmt.where(RecurringTransaction.state != "DISMISSED")
    rows = db.execute(stmt).all()
    items = [recurring_out(r, today, name) for r, name in rows]
    return sorted(items, key=lambda i: Decimal(i["monthly_equivalent"]))


def upcoming_recurring(db: Session, owner_id: uuid.UUID, today: date, days: int) -> list[dict[str, Any]]:
    horizon = today + timedelta(days=days)
    rows = db.execute(
        select(RecurringTransaction, Account.name)
        .join(Account, Account.id == RecurringTransaction.account_id)
        .where(
            RecurringTransaction.owner_id == owner_id,
            RecurringTransaction.state != "DISMISSED",
            RecurringTransaction.expected_next_date <= horizon,
        )
        .order_by(RecurringTransaction.expected_next_date)
        .limit(20)
    ).all()
    return [recurring_out(r, today, name) for r, name in rows]


def set_recurring_state(
    db: Session, owner_id: uuid.UUID, rid: uuid.UUID, state: str, label: str | None
) -> RecurringTransaction:
    from oneledger_shared.errors import NotFoundError

    r = db.get(RecurringTransaction, rid)
    if r is None or r.owner_id != owner_id:
        raise NotFoundError()
    r.state = state
    if label:
        r.label = label[:160]
    return r


# --- Budgets -------------------------------------------------------------------------------------


def budget_progress(db: Session, owner_id: uuid.UUID, budget: Budget, month: DateRange) -> dict[str, Any]:
    """Net expense (purchases minus refunds) in the budget's category subtree, counted once."""
    cat = load_context(db, owner_id)
    stmt = (
        select(func.coalesce(func.sum(TransactionAllocation.amount), 0), func.count(func.distinct(Transaction.id)))
        .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.currency == budget.currency,
            Transaction.transaction_date >= month.start,
            Transaction.transaction_date < month.end_exclusive,
            TransactionAllocation.effect == AllocationEffect.EXPENSE,
        )
    )
    if budget.category_id:
        stmt = stmt.where(TransactionAllocation.category_id.in_(cat.descendants(budget.category_id)))
    if budget.account_ids:
        stmt = stmt.where(Transaction.account_id.in_(budget.account_ids))
    total, count = db.execute(stmt).one()
    spent = -Decimal(total)
    remaining = budget.amount - spent
    return {
        "id": str(budget.id),
        "name": budget.name,
        "category_id": str(budget.category_id) if budget.category_id else None,
        "amount": str(budget.amount),
        "currency": budget.currency,
        "spent": str(spent),
        "remaining": str(remaining),
        "percent": str((spent / budget.amount * 100).quantize(Decimal("0.1"))) if budget.amount else "0",
        "over": spent > budget.amount,
        "transaction_count": int(count),
        "period_start": month.start,
        "period_end_exclusive": month.end_exclusive,
        "rollover": budget.rollover,
        "version": budget.version,
    }


def budgets_report(db: Session, owner_id: uuid.UUID, year: int, month: int) -> dict[str, Any]:
    rng = month_range(year, month)
    budgets = db.scalars(
        select(Budget).where(
            Budget.owner_id == owner_id, Budget.deleted_at.is_(None), Budget.start_date < rng.end_exclusive
        )
    ).all()
    items = [budget_progress(db, owner_id, b, rng) for b in budgets]
    warnings = []
    cats = [b.category_id for b in budgets if b.category_id]
    cat = load_context(db, owner_id)
    for b in budgets:
        if b.category_id and any(o != b.category_id and o in cat.descendants(b.category_id) for o in cats):
            warnings.append(f"Budget '{b.name}' overlaps a sub-category budget; totals are not additive.")
    qid, rev = record_query(
        db,
        owner_id,
        "budgets",
        {"start": rng.start.isoformat(), "end": rng.end_exclusive.isoformat(), "effect_filter": "expense"},
        "budgets-v1",
    )
    return {
        "data": {"month": f"{year:04d}-{month:02d}", "budgets": items},
        "provenance": {
            "query_id": str(qid),
            "ledger_revision": rev,
            "warnings": warnings,
            "calculation_version": "budgets-v1",
        },
    }


# --- Goals ---------------------------------------------------------------------------------------


def goal_progress(db: Session, owner_id: uuid.UUID, goal: FinancialGoal, today: date) -> dict[str, Any]:
    """Goals use either linked account balances or explicit contributions -- never both."""
    partial = False
    if goal.progress_mode == "ACCOUNTS":
        accounts = list(
            db.scalars(select(Account).where(Account.owner_id == owner_id, Account.id.in_(goal.account_ids)))
        )
        bals = account_balances(db, owner_id, today, accounts)
        known = [b.balance for b in bals if b.balance is not None and b.account.currency == goal.currency]
        partial = len(known) != len(bals)
        current = sum(known, Decimal(0))
    else:
        current = Decimal(
            db.scalar(
                select(func.coalesce(func.sum(GoalContribution.amount), 0)).where(GoalContribution.goal_id == goal.id)
            )
            or 0
        )
    pct = min(current / goal.target_amount * 100, Decimal(999)) if goal.target_amount else Decimal(0)
    monthly_needed = None
    if goal.target_date and goal.target_date > today and current < goal.target_amount:
        months = max((goal.target_date.year - today.year) * 12 + goal.target_date.month - today.month, 1)
        monthly_needed = str(((goal.target_amount - current) / months).quantize(Decimal("0.01")))
    return {
        "id": str(goal.id),
        "name": goal.name,
        "target_amount": str(goal.target_amount),
        "currency": goal.currency,
        "target_date": goal.target_date,
        "progress_mode": goal.progress_mode,
        "account_ids": [str(a) for a in goal.account_ids],
        "current": str(current),
        "percent": str(pct.quantize(Decimal("0.1"))),
        "monthly_needed": monthly_needed,
        "partial": partial,
        "version": goal.version,
    }


# --- Forecast ------------------------------------------------------------------------------------


def forecast_report(db: Session, owner_id: uuid.UUID, today: date, days: int) -> dict[str, Any]:
    user = db.get(User, owner_id)
    currency = user.base_currency if user else "INR"
    recs = db.scalars(
        select(RecurringTransaction).where(
            RecurringTransaction.owner_id == owner_id, RecurringTransaction.state == "ACCEPTED"
        )
    ).all()
    suggested = int(
        db.scalar(
            select(func.count())
            .select_from(RecurringTransaction)
            .where(RecurringTransaction.owner_id == owner_id, RecurringTransaction.state == "SUGGESTED")
        )
        or 0
    )
    schedules = [
        fc.Schedule(r.label, r.typical_amount, r.currency, r.cadence, r.expected_next_date, r.anchor_day) for r in recs
    ]
    result = fc.forecast(schedules, today, days)
    liquid = [
        a
        for a in db.scalars(select(Account).where(Account.owner_id == owner_id, Account.deleted_at.is_(None)))
        if a.kind.is_liquid and a.currency == currency
    ]
    bals = account_balances(db, owner_id, today, liquid)
    known = [b.balance for b in bals if b.balance is not None]
    start_balance = sum(known, Decimal(0)) if known else None
    totals = result.totals().get(currency, {"inflow": Decimal(0), "outflow": Decimal(0), "net": Decimal(0)})
    assumptions = list(result.assumptions)
    if suggested:
        assumptions.append(f"{suggested} detected recurring pattern(s) are not yet confirmed and are excluded.")
    if len(known) != len(bals):
        assumptions.append("Some liquid accounts have no known balance; the projected balance is partial.")
    if not recs:
        return {
            "data": {
                "available": False,
                "reason": "not_enough_data",
                "message": "No confirmed recurring items yet. Confirm recurring payments to enable a forecast.",
            },
            "assumptions": assumptions,
        }
    return {
        "data": {
            "available": True,
            "currency": currency,
            "days": days,
            "start": today,
            "end_exclusive": result.end_exclusive,
            "starting_liquid_balance": str(start_balance) if start_balance is not None else None,
            "inflow": str(totals["inflow"]),
            "outflow": str(totals["outflow"]),
            "net": str(totals["net"]),
            "projected_liquid_balance": str(start_balance + totals["net"]) if start_balance is not None else None,
            "items": [
                {"label": i.label, "date": i.due_date, "amount": str(i.amount), "currency": i.currency}
                for i in result.items
            ],
        },
        "assumptions": assumptions,
        "calculation_version": fc.FORECAST_VERSION,
    }
