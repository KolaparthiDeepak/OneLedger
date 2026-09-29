"""Analytics endpoints. All aggregates are computed server-side and carry provenance."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from oneledger_db.models import Transaction
from oneledger_domain.enums import AllocationEffect
from oneledger_domain.periods import DateRange, month_range, resolve_period, this_month, today_in
from oneledger_shared.errors import ValidationFailed
from sqlalchemy import func, select

from ..deps import ReadAuth
from ..services import reports

router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_RANGE_DAYS = 3700


def period_of(
    a: ReadAuth, start_date: date | None, end_date_exclusive: date | None, period: str | None
) -> tuple[DateRange, str, str]:
    user = a.user()
    tz, currency = user.timezone, user.base_currency
    try:
        if start_date and end_date_exclusive:
            rng = DateRange(start_date, end_date_exclusive)
        elif period:
            rng = resolve_period(period, tz)
        else:
            rng = this_month(tz)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="INVALID_PERIOD") from exc
    if rng.days > MAX_RANGE_DAYS:
        raise ValidationFailed("Date range is too long.", code="INVALID_RANGE")
    return rng, tz, currency


Accounts = Annotated[list[uuid.UUID] | None, Query()]


@router.get("/summary")
def summary(
    a: ReadAuth,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    period: str | None = None,
    account_id: Accounts = None,
) -> dict[str, Any]:
    rng, tz, cur = period_of(a, start_date, end_date_exclusive, period)
    out = reports.summary_report(a.db, a.owner_id, tz, cur, rng, account_id)
    a.commit()
    return out


@router.get("/categories")
def categories(
    a: ReadAuth,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    period: str | None = None,
    effect: AllocationEffect = AllocationEffect.EXPENSE,
    account_id: Accounts = None,
) -> dict[str, Any]:
    rng, tz, cur = period_of(a, start_date, end_date_exclusive, period)
    if effect not in (AllocationEffect.EXPENSE, AllocationEffect.INCOME, AllocationEffect.INVESTMENT):
        raise ValidationFailed("Breakdowns are available for expense, income and investment.", code="INVALID_EFFECT")
    out = reports.category_breakdown(a.db, a.owner_id, tz, cur, rng, effect, account_id)
    a.commit()
    return out


@router.get("/monthly")
def monthly(
    a: ReadAuth, months: Annotated[int, Query(ge=1, le=36)] = 12, account_id: Accounts = None
) -> dict[str, Any]:
    user = a.user()
    cur_month = this_month(user.timezone).start
    out = reports.monthly_series(a.db, a.owner_id, user.timezone, user.base_currency, cur_month, months, account_id)
    a.commit()
    return out


@router.get("/compare")
def compare(
    a: ReadAuth, start_date: date | None = None, end_date_exclusive: date | None = None, period: str | None = None
) -> dict[str, Any]:
    rng, tz, cur = period_of(a, start_date, end_date_exclusive, period)
    out = reports.compare_periods(a.db, a.owner_id, tz, cur, rng, rng.previous())
    a.commit()
    return out


@router.get("/cash-flow")
def cash_flow(
    a: ReadAuth,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    period: str | None = None,
    account_id: Accounts = None,
) -> dict[str, Any]:
    rng, tz, cur = period_of(a, start_date, end_date_exclusive, period)
    out = reports.cash_flow(a.db, a.owner_id, tz, cur, rng, account_id)
    a.commit()
    return out


@router.get("/balances")
def balances(a: ReadAuth, as_of: date | None = None) -> dict[str, Any]:
    user = a.user()
    out = reports.balances_report(a.db, a.owner_id, user.timezone, as_of or today_in(user.timezone))
    a.commit()
    return out


@router.get("/net-worth")
def net_worth(a: ReadAuth, as_of: date | None = None) -> dict[str, Any]:
    user = a.user()
    out = reports.net_worth_report(a.db, a.owner_id, user.timezone, as_of or today_in(user.timezone))
    a.commit()
    return out


@router.get("/net-worth/history")
def net_worth_history(a: ReadAuth, days: Annotated[int, Query(ge=7, le=1100)] = 365) -> dict[str, Any]:
    """Daily snapshots, plus month-end points rebuilt from balances for the time before snapshots began."""
    from oneledger_db.models import NetWorthSnapshot

    today = today_in(a.user().timezone)
    since = today - timedelta(days=days)
    rows = a.db.scalars(
        select(NetWorthSnapshot)
        .where(
            NetWorthSnapshot.owner_id == a.owner_id,
            NetWorthSnapshot.is_current.is_(True),
            NetWorthSnapshot.snapshot_date >= since,
        )
        .order_by(NetWorthSnapshot.snapshot_date)
    ).all()
    stored: list[dict[str, Any]] = [
        {
            "date": r.snapshot_date,
            "currency": r.currency,
            "net_worth": str(r.net_worth),
            "assets": str(r.assets),
            "liabilities": str(r.liabilities),
            "partial": r.partial,
            "stale": r.is_stale,
            "source": "snapshot",
        }
        for r in rows
    ]
    first_stored: date = min((r.snapshot_date for r in rows), default=today)
    computed = [
        p
        for p in reports.net_worth_backfill(a.db, a.owner_id, today, months=max(1, days // 30))
        if since <= p["date"] < first_stored
    ]
    return {"points": sorted(computed + stored, key=lambda p: (p["date"], p["currency"]))}


@router.get("/dashboard")
def dashboard(
    a: ReadAuth, month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None
) -> dict[str, Any]:
    """One round-trip for the home screen; each section keeps its own provenance.

    ``month`` (YYYY-MM) picks the month shown; without it, the current month, or the latest month
    with data when nothing is recorded yet this month.
    """
    from ..services import insights
    from ..services.planning import budgets_report, upcoming_recurring
    from ..services.review import open_counts

    user = a.user()
    tz, cur = user.timezone, user.base_currency
    today = today_in(tz)
    period_note = None
    latest = a.db.scalar(
        select(func.max(Transaction.transaction_date)).where(
            Transaction.owner_id == a.owner_id, reports.active_txn_filter()
        )
    )
    earliest = a.db.scalar(
        select(func.min(Transaction.transaction_date)).where(
            Transaction.owner_id == a.owner_id, reports.active_txn_filter()
        )
    )
    month_ = month_range(int(month[:4]), int(month[5:])) if month else this_month(tz)
    if month is None and latest is not None and latest < month_.start:
        # Nothing recorded yet this month: show the latest month that has data, and say so.
        month_ = month_range(latest.year, latest.month)
        period_note = f"No transactions yet this month; showing {latest:%B %Y}, the latest month with data."
    prev = month_.previous()
    safe = insights.safe_to_spend(a.db, a.owner_id, today, cur)
    out = {
        "as_of": today.isoformat(),
        "period": {
            "start": month_.start.isoformat(),
            "end_exclusive": month_.end_exclusive.isoformat(),
            "note": period_note,
        },
        "summary": reports.summary_report(a.db, a.owner_id, tz, cur, month_),
        "previous_summary": reports.summary_report(a.db, a.owner_id, tz, cur, prev),
        "categories": reports.category_breakdown(a.db, a.owner_id, tz, cur, month_),
        "monthly": reports.monthly_series(a.db, a.owner_id, tz, cur, month_.start, 6),
        "balances": reports.balances_report(a.db, a.owner_id, tz, today),
        "net_worth": reports.net_worth_report(a.db, a.owner_id, tz, today),
        "recurring": upcoming_recurring(a.db, a.owner_id, today, 45),
        "review_counts": open_counts(a.db, a.owner_id),
        "bills": insights.upcoming_bills(a.db, a.owner_id, today, 21),
        "safe_to_spend": safe,
        "budgets": budgets_report(a.db, a.owner_id, month_.start.year, month_.start.month)["data"]["budgets"],
        "alerts": insights.alerts(a.db, a.owner_id, today, cur, safe=safe),
        "data_range": {"first": earliest, "last": latest},
    }
    a.commit()
    return out
