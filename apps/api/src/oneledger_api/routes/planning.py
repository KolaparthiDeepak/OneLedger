"""Recurring payments, budgets, goals, forecasts and anomalies."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from oneledger_db.models import (
    Account,
    Anomaly,
    Budget,
    FinancialGoal,
    GoalContribution,
    Transaction,
    TransactionCategory,
)
from oneledger_domain.money import validate_precision
from oneledger_domain.periods import today_in
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import MoneyIn, ReadAuth, WriteAuth
from ..services import planning
from ..services.analytics_jobs import refresh_anomalies, refresh_recurring
from ..services.audit import audit

router = APIRouter(tags=["planning"])


@router.get("/recurring")
def recurring(a: ReadAuth, include_dismissed: bool = False) -> list[dict[str, Any]]:
    return planning.list_recurring(a.db, a.owner_id, today_in(a.user().timezone), include_dismissed)


@router.post("/recurring/detect")
def detect(a: WriteAuth) -> dict[str, int]:
    n = refresh_recurring(a.db, a.owner_id, today_in(a.user().timezone))
    a.commit()
    return {"patterns": n}


class RecurringDecision(BaseModel):
    state: Literal["ACCEPTED", "DISMISSED", "SUGGESTED"]
    label: str | None = Field(default=None, max_length=160)


@router.post("/recurring/{rid}")
def decide(rid: uuid.UUID, body: RecurringDecision, a: WriteAuth) -> dict[str, Any]:
    r = planning.set_recurring_state(a.db, a.owner_id, rid, body.state, body.label)
    audit(a.db, a.owner_id, a.actor, "recurring.decide", "recurring", r.id, ["state"])
    a.commit()
    return planning.recurring_out(r, today_in(a.user().timezone))


# --- Budgets -------------------------------------------------------------------------------------


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category_id: uuid.UUID | None = None
    account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    amount: MoneyIn
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    start_date: date | None = None
    rollover: bool = False


@router.get("/budgets")
def budgets(a: ReadAuth, month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None) -> dict[str, Any]:
    today = today_in(a.user().timezone)
    y, m = (int(month[:4]), int(month[5:])) if month else (today.year, today.month)
    out = planning.budgets_report(a.db, a.owner_id, y, m)
    a.commit()
    return out


def _check_refs(a: WriteAuth, category_id: uuid.UUID | None, account_ids: list[uuid.UUID]) -> None:
    if category_id:
        c = a.db.get(TransactionCategory, category_id)
        if c is None or c.owner_id != a.owner_id:
            raise NotFoundError("Category not found.")
    if account_ids:
        found = set(a.db.scalars(select(Account.id).where(Account.owner_id == a.owner_id, Account.id.in_(account_ids))))
        if found != set(account_ids):
            raise NotFoundError("Account not found.")


@router.post("/budgets", status_code=201)
def create_budget(body: BudgetIn, a: WriteAuth) -> dict[str, Any]:
    user = a.user()
    currency = (body.currency or user.base_currency).upper()
    validate_precision(body.amount, currency)
    if body.amount <= 0:
        raise ValidationFailed("Budget amount must be positive.", code="INVALID_AMOUNT")
    _check_refs(a, body.category_id, body.account_ids)
    today = today_in(user.timezone)
    b = Budget(
        owner_id=a.owner_id,
        name=body.name,
        category_id=body.category_id,
        account_ids=body.account_ids,
        amount=body.amount,
        currency=currency,
        start_date=body.start_date or date(today.year, today.month, 1),
        rollover=body.rollover,
    )
    a.db.add(b)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "budget.create", "budget", b.id, ["amount"])
    a.commit()
    return {"id": str(b.id)}


class BudgetPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    amount: MoneyIn | None = None
    rollover: bool | None = None


@router.patch("/budgets/{budget_id}")
def update_budget(budget_id: uuid.UUID, body: BudgetPatch, a: WriteAuth) -> dict[str, Any]:
    b = a.db.get(Budget, budget_id)
    if b is None or b.owner_id != a.owner_id or b.deleted_at:
        raise NotFoundError()
    if b.version != body.version:
        raise ConflictError("This budget changed since you loaded it.", code="VERSION_CONFLICT")
    if body.name:
        b.name = body.name
    if body.amount is not None:
        if body.amount <= 0:
            raise ValidationFailed("Budget amount must be positive.", code="INVALID_AMOUNT")
        b.amount = body.amount
    if body.rollover is not None:
        b.rollover = body.rollover
    b.version += 1
    audit(a.db, a.owner_id, a.actor, "budget.update", "budget", b.id, ["name", "amount", "rollover"])
    a.commit()
    return {"id": str(b.id), "version": b.version}


@router.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(budget_id: uuid.UUID, a: WriteAuth) -> None:
    b = a.db.get(Budget, budget_id)
    if b is None or b.owner_id != a.owner_id:
        raise NotFoundError()
    b.deleted_at = datetime.now(UTC)
    audit(a.db, a.owner_id, a.actor, "budget.delete", "budget", b.id, ["deleted_at"])
    a.commit()


# --- Goals ---------------------------------------------------------------------------------------


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_amount: MoneyIn
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    target_date: date | None = None
    progress_mode: Literal["ACCOUNTS", "MANUAL"] = "MANUAL"
    account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


@router.get("/goals")
def goals(a: ReadAuth) -> list[dict[str, Any]]:
    today = today_in(a.user().timezone)
    rows = a.db.scalars(
        select(FinancialGoal)
        .where(FinancialGoal.owner_id == a.owner_id, FinancialGoal.deleted_at.is_(None))
        .order_by(FinancialGoal.created_at)
    )
    return [planning.goal_progress(a.db, a.owner_id, g, today) for g in rows]


@router.post("/goals", status_code=201)
def create_goal(body: GoalIn, a: WriteAuth) -> dict[str, Any]:
    user = a.user()
    if body.target_amount <= 0:
        raise ValidationFailed("Target must be positive.", code="INVALID_AMOUNT")
    if body.progress_mode == "ACCOUNTS" and not body.account_ids:
        raise ValidationFailed("Choose at least one account to track.", code="ACCOUNTS_REQUIRED")
    if body.progress_mode == "MANUAL" and body.account_ids:
        raise ValidationFailed("Manual goals track contributions, not accounts.", code="MODE_CONFLICT")
    _check_refs(a, None, body.account_ids)
    g = FinancialGoal(
        owner_id=a.owner_id,
        name=body.name,
        target_amount=body.target_amount,
        currency=(body.currency or user.base_currency).upper(),
        target_date=body.target_date,
        progress_mode=body.progress_mode,
        account_ids=body.account_ids,
    )
    a.db.add(g)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "goal.create", "goal", g.id, ["target_amount"])
    a.commit()
    return planning.goal_progress(a.db, a.owner_id, g, today_in(user.timezone))


class ContributionIn(BaseModel):
    amount: MoneyIn
    contribution_date: date
    note: str | None = Field(default=None, max_length=200)


@router.post("/goals/{goal_id}/contributions", status_code=201)
def contribute(goal_id: uuid.UUID, body: ContributionIn, a: WriteAuth) -> dict[str, Any]:
    g = a.db.get(FinancialGoal, goal_id)
    if g is None or g.owner_id != a.owner_id or g.deleted_at:
        raise NotFoundError()
    if g.progress_mode != "MANUAL":
        raise ValidationFailed("This goal tracks account balances; contributions are not used.", code="MODE_CONFLICT")
    a.db.add(
        GoalContribution(
            owner_id=a.owner_id,
            goal_id=g.id,
            amount=body.amount,
            contribution_date=body.contribution_date,
            note=body.note,
        )
    )
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "goal.contribute", "goal", g.id, ["contributions"])
    a.commit()
    return planning.goal_progress(a.db, a.owner_id, g, today_in(a.user().timezone))


class GoalPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target_amount: MoneyIn | None = None
    target_date: date | None = None
    clear_target_date: bool = False


@router.patch("/goals/{goal_id}")
def update_goal(goal_id: uuid.UUID, body: GoalPatch, a: WriteAuth) -> dict[str, Any]:
    g = a.db.get(FinancialGoal, goal_id)
    if g is None or g.owner_id != a.owner_id or g.deleted_at:
        raise NotFoundError()
    if g.version != body.version:
        raise ConflictError("This goal changed since you loaded it.", code="VERSION_CONFLICT")
    if body.name:
        g.name = body.name
    if body.target_amount is not None:
        if body.target_amount <= 0:
            raise ValidationFailed("Target must be positive.", code="INVALID_AMOUNT")
        g.target_amount = body.target_amount
    if body.clear_target_date:
        g.target_date = None
    elif body.target_date is not None:
        g.target_date = body.target_date
    g.version += 1
    audit(a.db, a.owner_id, a.actor, "goal.update", "goal", g.id, ["name", "target_amount", "target_date"])
    a.commit()
    return planning.goal_progress(a.db, a.owner_id, g, today_in(a.user().timezone))


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: uuid.UUID, a: WriteAuth) -> None:
    g = a.db.get(FinancialGoal, goal_id)
    if g is None or g.owner_id != a.owner_id:
        raise NotFoundError()
    g.deleted_at = datetime.now(UTC)
    audit(a.db, a.owner_id, a.actor, "goal.delete", "goal", g.id, ["deleted_at"])
    a.commit()


# --- Forecast & anomalies ------------------------------------------------------------------------


@router.get("/forecast")
def forecast(a: ReadAuth, days: Annotated[int, Query(ge=7, le=180)] = 30) -> dict[str, Any]:
    return planning.forecast_report(a.db, a.owner_id, today_in(a.user().timezone), days)


@router.get("/anomalies")
def anomalies(a: ReadAuth, include_dismissed: bool = False) -> list[dict[str, Any]]:
    stmt = (
        select(Anomaly, Transaction)
        .join(Transaction, Transaction.id == Anomaly.transaction_id)
        .where(Anomaly.owner_id == a.owner_id)
    )
    if not include_dismissed:
        stmt = stmt.where(Anomaly.dismissed_at.is_(None))
    rows = a.db.execute(stmt.order_by(Transaction.transaction_date.desc()).limit(200)).all()
    return [
        {
            "id": str(an.id),
            "rule": an.rule,
            "rule_version": an.rule_version,
            "reason": an.reason,
            "evidence": an.evidence,
            "transaction_id": str(t.id),
            "date": t.transaction_date,
            "amount": str(t.amount),
            "currency": t.currency,
            "merchant": t.merchant_name,
            "dismissed": an.dismissed_at is not None,
        }
        for an, t in rows
    ]


@router.post("/anomalies/detect")
def detect_anomalies(a: WriteAuth) -> dict[str, int]:
    n = refresh_anomalies(a.db, a.owner_id, today_in(a.user().timezone))
    a.commit()
    return {"new": n}


@router.post("/anomalies/{anomaly_id}/dismiss")
def dismiss_anomaly(anomaly_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    an = a.db.get(Anomaly, anomaly_id)
    if an is None or an.owner_id != a.owner_id:
        raise NotFoundError()
    an.dismissed_at = datetime.now(UTC)
    audit(a.db, a.owner_id, a.actor, "anomaly.dismiss", "anomaly", an.id, ["dismissed_at"])
    a.commit()
    return {"status": "dismissed"}
