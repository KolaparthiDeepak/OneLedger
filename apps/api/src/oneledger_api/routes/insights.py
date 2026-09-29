"""Calendar totals, upcoming bills, safe to spend, alerts, cash-flow diagram, budget suggestions."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from oneledger_domain.periods import today_in
from pydantic import BaseModel, Field

from ..deps import ReadAuth, WriteAuth
from ..services import insights
from ..services.audit import audit
from .analytics import Accounts, period_of

router = APIRouter(tags=["insights"])


@router.get("/analytics/daily")
def daily(
    a: ReadAuth,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    period: str | None = None,
    account_id: Accounts = None,
) -> dict[str, Any]:
    rng, _tz, cur = period_of(a, start_date, end_date_exclusive, period)
    return {
        "start_date": rng.start,
        "end_date_exclusive": rng.end_exclusive,
        "currency": cur,
        "days": insights.daily_totals(a.db, a.owner_id, rng, cur, account_id),
    }


@router.get("/analytics/sankey")
def sankey(
    a: ReadAuth, start_date: date | None = None, end_date_exclusive: date | None = None, period: str | None = None
) -> dict[str, Any]:
    rng, tz, cur = period_of(a, start_date, end_date_exclusive, period)
    out = insights.cash_flow_sankey(a.db, a.owner_id, tz, cur, rng)
    a.commit()
    return {**out, "start_date": rng.start, "end_date_exclusive": rng.end_exclusive}


@router.get("/insights/upcoming")
def upcoming(a: ReadAuth, days: Annotated[int, Query(ge=1, le=120)] = 35) -> list[dict[str, Any]]:
    return insights.upcoming_bills(a.db, a.owner_id, today_in(a.user().timezone), days)


@router.get("/insights/safe-to-spend")
def safe_to_spend(a: ReadAuth) -> dict[str, Any]:
    user = a.user()
    return insights.safe_to_spend(a.db, a.owner_id, today_in(user.timezone), user.base_currency)


@router.get("/insights/alerts")
def alerts(a: ReadAuth) -> list[dict[str, Any]]:
    user = a.user()
    out = insights.alerts(a.db, a.owner_id, today_in(user.timezone), user.base_currency)
    a.commit()
    return out


class DismissIn(BaseModel):
    key: str = Field(min_length=3, max_length=200)


@router.post("/insights/alerts/dismiss")
def dismiss(body: DismissIn, a: WriteAuth) -> dict[str, str]:
    insights.dismiss_alert(a.db, a.owner_id, body.key)
    audit(a.db, a.owner_id, a.actor, "alert.dismiss", "alert", None, ["dismissed"])
    a.commit()
    return {"status": "dismissed"}


@router.get("/budgets/suggestions")
def budget_suggestions(a: ReadAuth) -> list[dict[str, Any]]:
    user = a.user()
    out = insights.budget_suggestions(a.db, a.owner_id, user.timezone, user.base_currency, today_in(user.timezone))
    a.commit()
    return out
