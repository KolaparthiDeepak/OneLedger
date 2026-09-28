"""Analytics endpoints. All aggregates are computed server-side and carry provenance."""

from __future__ import annotations

import uuid
from datetime import date
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
    from oneledger_db.models import NetWorthSnapshot

    rows = a.db.scalars(
        select(NetWorthSnapshot)
        .where(NetWorthSnapshot.owner_id == a.owner_id, NetWorthSnapshot.is_current.is_(True))
        .order_by(NetWorthSnapshot.snapshot_date.desc())
        .limit(days * 2)
    ).all()
    return {
        "points": [
            {
                "date": r.snapshot_date,
                "currency": r.currency,
                "net_worth": str(r.net_worth),
                "assets": str(r.assets),
                "liabilities": str(r.liabilities),
                "partial": r.partial,
                "stale": r.is_stale,
            }
            for r in reversed(rows)
        ]
    }


@router.get("/dashboard")
def dashboard(a: ReadAuth) -> dict[str, Any]:
    """One round-trip for the home screen; each section keeps its own provenance."""
    from ..services.planning import upcoming_recurring
    from ..services.review import open_counts

    user = a.user()
    tz, cur = user.timezone, user.base_currency
    today = today_in(tz)
    month = this_month(tz)
    period_note = None
    latest = a.db.scalar(
        select(func.max(Transaction.transaction_date)).where(
            Transaction.owner_id == a.owner_id, reports.active_txn_filter()
        )
    )
    if latest is not None and latest < month.start:
        # Nothing recorded yet this month: show the latest month that has data, and say so.
        month = month_range(latest.year, latest.month)
        period_note = f"No transactions yet this month; showing {latest:%B %Y}, the latest month with data."
    prev = month.previous()
    out = {
        "as_of": today.isoformat(),
        "period": {
            "start": month.start.isoformat(),
            "end_exclusive": month.end_exclusive.isoformat(),
            "note": period_note,
        },
        "summary": reports.summary_report(a.db, a.owner_id, tz, cur, month),
        "previous_summary": reports.summary_report(a.db, a.owner_id, tz, cur, prev),
        "categories": reports.category_breakdown(a.db, a.owner_id, tz, cur, month),
        "monthly": reports.monthly_series(a.db, a.owner_id, tz, cur, month.start, 6),
        "balances": reports.balances_report(a.db, a.owner_id, tz, today),
        "net_worth": reports.net_worth_report(a.db, a.owner_id, tz, today),
        "recurring": upcoming_recurring(a.db, a.owner_id, today, 45),
        "review_counts": open_counts(a.db, a.owner_id),
    }
    a.commit()
    return out
