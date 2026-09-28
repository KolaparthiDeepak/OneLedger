"""Loans, credit cards and investments."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter
from oneledger_db.models import (
    Account,
    BalanceSnapshot,
    CreditCard,
    CreditCardStatement,
    Investment,
    InvestmentTransaction,
    InvestmentValuation,
    Loan,
    LoanRateChange,
)
from oneledger_domain import loans as ln
from oneledger_domain.enums import AccountKind, BalanceKind, BalanceSource, InstrumentType, InvestmentAction
from oneledger_domain.money import validate_precision
from oneledger_domain.periods import today_in
from oneledger_shared.errors import NotFoundError, ValidationFailed
from oneledger_shared.logging import mask_account_number
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import MoneyIn, ReadAuth, WriteAuth
from ..services import products as svc
from ..services.audit import audit, bump_ledger_revision
from ..services.categories import load_context
from ..services.ledger import get_account

router = APIRouter(tags=["products"])


# --- Loans ---------------------------------------------------------------------------------------


class LoanIn(BaseModel):
    lender: str = Field(min_length=1, max_length=120)
    loan_type: str = Field(default="Home Loan", max_length=40)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    original_principal: MoneyIn
    opening_outstanding: MoneyIn = Field(description="Outstanding principal at end of opening_date.")
    opening_date: date
    start_date: date
    first_emi_date: date
    tenure_months: int = Field(ge=1, le=600)
    annual_rate_percent: MoneyIn = Field(ge=0, le=60)
    emi_amount: MoneyIn | None = None


@router.get("/loans")
def loans(a: ReadAuth) -> list[dict[str, Any]]:
    today = today_in(a.user().timezone)
    rows = a.db.scalars(select(Loan).where(Loan.owner_id == a.owner_id, Loan.deleted_at.is_(None)))
    return [svc.loan_summary(a.db, a.owner_id, loan, today) for loan in rows]


@router.post("/loans", status_code=201)
def create_loan(body: LoanIn, a: WriteAuth) -> dict[str, Any]:
    loan = svc.create_loan(
        a.db,
        a.owner_id,
        lender=body.lender,
        loan_type=body.loan_type,
        currency=body.currency.upper(),
        original_principal=body.original_principal,
        opening_outstanding=body.opening_outstanding,
        opening_date=body.opening_date,
        start_date=body.start_date,
        first_emi_date=body.first_emi_date,
        tenure_months=body.tenure_months,
        annual_rate=body.annual_rate_percent,
        emi_amount=body.emi_amount,
        actor=a.actor,
    )
    a.commit()
    return svc.loan_summary(a.db, a.owner_id, loan, today_in(a.user().timezone))


@router.get("/loans/{loan_id}")
def loan_detail(loan_id: uuid.UUID, a: ReadAuth) -> dict[str, Any]:
    from oneledger_db.models import LoanPayment

    loan = svc.get_loan(a.db, a.owner_id, loan_id)
    out = svc.loan_summary(a.db, a.owner_id, loan, today_in(a.user().timezone))
    out["payments"] = [
        {
            "id": str(p.id),
            "date": p.payment_date,
            "principal": str(p.principal),
            "interest": str(p.interest),
            "fees": str(p.fees),
            "prepayment": str(p.prepayment),
            "actual": p.is_actual,
            "transaction_id": str(p.transaction_id) if p.transaction_id else None,
        }
        for p in a.db.scalars(
            select(LoanPayment)
            .where(LoanPayment.loan_id == loan.id, LoanPayment.deleted_at.is_(None))
            .order_by(LoanPayment.payment_date.desc())
        )
    ]
    out["rate_history"] = [
        {"effective_date": r.effective_date, "annual_rate_percent": svc.rate_str(r.annual_rate_percent)}
        for r in a.db.scalars(
            select(LoanRateChange).where(LoanRateChange.loan_id == loan.id).order_by(LoanRateChange.effective_date)
        )
    ]
    return out


@router.get("/loans/{loan_id}/schedule")
def loan_schedule(loan_id: uuid.UUID, a: ReadAuth) -> dict[str, Any]:
    loan = svc.get_loan(a.db, a.owner_id, loan_id)
    summary = svc.loan_summary(a.db, a.owner_id, loan, today_in(a.user().timezone))
    if summary["outstanding_principal"] is None:
        raise ValidationFailed("Outstanding principal is unknown.", code="OUTSTANDING_UNKNOWN")
    proj = summary["projection"] or {}
    if "error" in proj:
        raise ValidationFailed(proj["error"], code="LOAN_UNSUPPORTED")
    today = today_in(a.user().timezone)
    sched = ln.build_schedule(
        Decimal(summary["outstanding_principal"]),
        svc.current_rate(a.db, loan, today),
        max(loan.tenure_months - summary["payments_recorded"], 1),
        proj["next_emi_date"],
        emi=loan.emi_amount,
        rate_changes=svc._future_changes(a.db, loan, today),
    )
    return {
        "projection": True,
        "assumptions": sched.assumptions,
        "total_interest": str(sched.total_interest),
        "rows": [
            {
                "period": r.period,
                "due_date": r.due_date,
                "opening": str(r.opening_principal),
                "rate": svc.rate_str(r.rate_percent),
                "emi": str(r.emi),
                "interest": str(r.interest),
                "principal": str(r.principal),
                "closing": str(r.closing_principal),
            }
            for r in sched.rows
        ],
    }


class RateChangeIn(BaseModel):
    annual_rate_percent: MoneyIn = Field(ge=0, le=60)
    effective_date: date


@router.post("/loans/{loan_id}/rate-changes", status_code=201)
def add_rate(loan_id: uuid.UUID, body: RateChangeIn, a: WriteAuth) -> dict[str, str]:
    loan = svc.get_loan(a.db, a.owner_id, loan_id)
    existing = a.db.scalars(
        select(LoanRateChange).where(
            LoanRateChange.loan_id == loan.id, LoanRateChange.effective_date == body.effective_date
        )
    ).first()
    if existing:
        existing.annual_rate_percent = body.annual_rate_percent
    else:
        a.db.add(
            LoanRateChange(
                owner_id=a.owner_id,
                loan_id=loan.id,
                annual_rate_percent=body.annual_rate_percent,
                effective_date=body.effective_date,
            )
        )
    audit(a.db, a.owner_id, a.actor, "loan.rate_change", "loan", loan.id, ["rate"])
    a.commit()
    return {"status": "ok"}


class LoanPaymentIn(BaseModel):
    transaction_id: uuid.UUID
    principal: MoneyIn | None = None
    interest: MoneyIn | None = None
    fees: MoneyIn = Decimal(0)
    prepayment: bool = False


@router.post("/loans/{loan_id}/payments", status_code=201)
def loan_payment(loan_id: uuid.UUID, body: LoanPaymentIn, a: WriteAuth) -> dict[str, Any]:
    loan = svc.get_loan(a.db, a.owner_id, loan_id)
    cat = load_context(a.db, a.owner_id)
    p = svc.record_loan_payment(
        a.db,
        a.owner_id,
        loan,
        cat,
        transaction_id=body.transaction_id,
        principal=body.principal,
        interest=body.interest,
        fees=body.fees,
        is_prepayment=body.prepayment,
        actor=a.actor,
    )
    a.commit()
    return {
        "id": str(p.id),
        "principal": str(p.principal),
        "interest": str(p.interest),
        "fees": str(p.fees),
        "prepayment": str(p.prepayment),
        "actual": p.is_actual,
    }


class SimulateIn(BaseModel):
    amount: MoneyIn
    payment_date: date
    strategy: ln.PrepaymentStrategy = ln.PrepaymentStrategy.REDUCE_TENURE


@router.post("/loans/{loan_id}/simulate-prepayment")
def simulate(loan_id: uuid.UUID, body: SimulateIn, a: ReadAuth) -> dict[str, Any]:
    """Scenario only: never changes the recorded outstanding principal."""
    loan = svc.get_loan(a.db, a.owner_id, loan_id)
    s = svc.loan_summary(a.db, a.owner_id, loan, today_in(a.user().timezone))
    if s["outstanding_principal"] is None or not s["projection"] or "error" in s["projection"]:
        raise ValidationFailed("Outstanding principal is unknown.", code="OUTSTANDING_UNKNOWN")
    try:
        r = ln.simulate_prepayment(
            Decimal(s["outstanding_principal"]),
            Decimal(s["interest_rate_percent"]),
            max(loan.tenure_months - s["payments_recorded"], 1),
            s["projection"]["next_emi_date"],
            ln.Prepayment(body.payment_date, body.amount),
            body.strategy,
            emi=loan.emi_amount,
        )
    except ln.LoanModelError as exc:
        raise ValidationFailed(str(exc), code="LOAN_UNSUPPORTED") from exc
    return {
        "scenario": True,
        "strategy": r.strategy.value,
        "baseline_interest": str(r.baseline_interest),
        "scenario_interest": str(r.scenario_interest),
        "interest_saved": str(r.interest_saved),
        "baseline_payments": r.baseline_payments,
        "scenario_payments": r.scenario_payments,
        "months_saved": r.months_saved,
        "baseline_emi": str(r.baseline_emi),
        "scenario_emi": str(r.scenario_final_emi),
        "assumptions": ["Fixed current rate; monthly reducing balance; lender terms may differ."],
    }


# --- Credit cards --------------------------------------------------------------------------------


class CardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    issuer: str = Field(min_length=1, max_length=120)
    network: str | None = Field(default=None, max_length=30)
    card_number: str | None = Field(default=None, max_length=25, description="Only the last 4 digits are kept.")
    currency: str = Field(default="INR", min_length=3, max_length=3)
    credit_limit: MoneyIn | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_days: int | None = Field(default=None, ge=1, le=60)
    outstanding: MoneyIn | None = None
    outstanding_as_of: date | None = None


@router.get("/cards")
def cards(a: ReadAuth) -> list[dict[str, Any]]:
    today = today_in(a.user().timezone)
    return [
        svc.card_summary(a.db, a.owner_id, c, today)
        for c in a.db.scalars(select(CreditCard).where(CreditCard.owner_id == a.owner_id))
    ]


@router.post("/cards", status_code=201)
def create_card(body: CardIn, a: WriteAuth) -> dict[str, Any]:
    currency = body.currency.upper()
    acct = Account(
        owner_id=a.owner_id,
        name=body.name,
        kind=AccountKind.CREDIT_CARD,
        currency=currency,
        masked_identifier=mask_account_number(body.card_number),
        opening_date=body.outstanding_as_of,
    )
    a.db.add(acct)
    a.db.flush()
    if body.outstanding is not None:
        if body.outstanding_as_of is None:
            raise ValidationFailed("Outstanding needs an as-of date.", code="OPENING_DATE_REQUIRED")
        validate_precision(body.outstanding, currency)
        a.db.add(
            BalanceSnapshot(
                owner_id=a.owner_id,
                account_id=acct.id,
                amount=body.outstanding,
                currency=currency,
                balance_kind=BalanceKind.OPENING,
                as_of=body.outstanding_as_of,
                source=BalanceSource.MANUAL,
                is_observed=True,
            )
        )
    card = CreditCard(
        owner_id=a.owner_id,
        account_id=acct.id,
        issuer=body.issuer,
        network=body.network,
        credit_limit=body.credit_limit,
        statement_day=body.statement_day,
        payment_due_days=body.payment_due_days,
    )
    a.db.add(card)
    a.db.flush()
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "card.create", "card", card.id, ["issuer", "limit"])
    a.commit()
    return svc.card_summary(a.db, a.owner_id, card, today_in(a.user().timezone))


@router.post("/accounts/{account_id}/card-details", status_code=201)
def attach_card_details(account_id: uuid.UUID, body: CardIn, a: WriteAuth) -> dict[str, Any]:
    acct = get_account(a.db, a.owner_id, account_id)
    if acct.kind != AccountKind.CREDIT_CARD:
        raise ValidationFailed("Account is not a credit card.", code="NOT_A_CARD")
    card = a.db.scalars(select(CreditCard).where(CreditCard.account_id == acct.id)).first()
    if card is None:
        card = CreditCard(owner_id=a.owner_id, account_id=acct.id, issuer=body.issuer)
        a.db.add(card)
    card.issuer, card.network, card.credit_limit = body.issuer, body.network, body.credit_limit
    card.statement_day, card.payment_due_days = body.statement_day, body.payment_due_days
    a.db.flush()
    a.commit()
    return svc.card_summary(a.db, a.owner_id, card, today_in(a.user().timezone))


class StatementIn(BaseModel):
    period_start: date
    period_end: date
    statement_balance: MoneyIn
    minimum_due: MoneyIn | None = None
    due_date: date


@router.get("/cards/{card_id}/statements")
def statements(card_id: uuid.UUID, a: ReadAuth) -> list[dict[str, Any]]:
    card = a.db.get(CreditCard, card_id)
    if card is None or card.owner_id != a.owner_id:
        raise NotFoundError()
    today = today_in(a.user().timezone)
    return [
        svc.statement_status(a.db, a.owner_id, card, s, today)
        for s in a.db.scalars(
            select(CreditCardStatement)
            .where(CreditCardStatement.card_id == card.id)
            .order_by(CreditCardStatement.period_end.desc())
        )
    ]


@router.post("/cards/{card_id}/statements", status_code=201)
def add_statement(card_id: uuid.UUID, body: StatementIn, a: WriteAuth) -> dict[str, Any]:
    """A statement summary records the balance owed; it never creates duplicate transactions."""
    card = a.db.get(CreditCard, card_id)
    if card is None or card.owner_id != a.owner_id:
        raise NotFoundError()
    if body.period_end < body.period_start or body.due_date < body.period_end:
        raise ValidationFailed("Check the statement dates.", code="INVALID_DATES")
    stmt = CreditCardStatement(
        owner_id=a.owner_id,
        card_id=card.id,
        period_start=body.period_start,
        period_end=body.period_end,
        statement_balance=body.statement_balance,
        minimum_due=body.minimum_due,
        due_date=body.due_date,
    )
    a.db.add(stmt)
    acct = a.db.get(Account, card.account_id)
    assert acct is not None
    snap = BalanceSnapshot(
        owner_id=a.owner_id,
        account_id=card.account_id,
        amount=body.statement_balance,
        currency=acct.currency,
        balance_kind=BalanceKind.STATEMENT,
        as_of=body.period_end,
        source=BalanceSource.MANUAL,
        is_observed=True,
        source_ref="card_statement",
    )
    a.db.add(snap)
    a.db.flush()
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "card.statement", "card", card.id, ["statement"])
    a.commit()
    return svc.statement_status(a.db, a.owner_id, card, stmt, today_in(a.user().timezone))


# --- Investments ---------------------------------------------------------------------------------


class InvestmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    instrument_type: InstrumentType
    identifier: str | None = Field(default=None, max_length=40)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    valuation_mode: Literal["UNITS", "MANUAL_TOTAL"] = "MANUAL_TOTAL"
    account_id: uuid.UUID | None = None
    include_in_net_worth: bool = True


@router.get("/investments")
def investments(a: ReadAuth) -> dict[str, Any]:
    today = today_in(a.user().timezone)
    items = [
        svc.holding_summary(a.db, i, today)
        for i in a.db.scalars(
            select(Investment)
            .where(Investment.owner_id == a.owner_id, Investment.deleted_at.is_(None))
            .order_by(Investment.name)
        )
    ]
    totals: dict[str, dict[str, Decimal]] = {}
    unvalued = 0
    for i in items:
        t = totals.setdefault(i["currency"], {"value": Decimal(0), "net_contributions": Decimal(0)})
        t["net_contributions"] += Decimal(i["net_contributions"])
        if i["value"] is None:
            unvalued += 1
        else:
            t["value"] += Decimal(i["value"])
    return {
        "items": items,
        "totals": {c: {k: str(v) for k, v in t.items()} for c, t in totals.items()},
        "unvalued_count": unvalued,
        "note": "Values are dated manual valuations; no live market prices are fetched.",
    }


@router.post("/investments", status_code=201)
def create_investment(body: InvestmentIn, a: WriteAuth) -> dict[str, Any]:
    if body.account_id:
        get_account(a.db, a.owner_id, body.account_id)
    inv = Investment(
        owner_id=a.owner_id,
        account_id=body.account_id,
        instrument_type=body.instrument_type,
        name=body.name,
        identifier=body.identifier,
        currency=body.currency.upper(),
        valuation_mode=body.valuation_mode,
        include_in_net_worth=body.include_in_net_worth,
    )
    a.db.add(inv)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "investment.create", "investment", inv.id, ["name"])
    a.commit()
    return svc.holding_summary(a.db, inv, today_in(a.user().timezone))


def _inv(a: ReadAuth | WriteAuth, inv_id: uuid.UUID) -> Investment:
    inv = a.db.get(Investment, inv_id)
    if inv is None or inv.owner_id != a.owner_id or inv.deleted_at:
        raise NotFoundError()
    return inv


class InvTxnIn(BaseModel):
    action: InvestmentAction
    trade_date: date
    units: MoneyIn | None = None
    unit_price: MoneyIn | None = None
    gross_amount: MoneyIn = Field(gt=0)
    fees: MoneyIn = Decimal(0)
    ledger_transaction_id: uuid.UUID | None = None


@router.post("/investments/{inv_id}/transactions", status_code=201)
def add_inv_txn(inv_id: uuid.UUID, body: InvTxnIn, a: WriteAuth) -> dict[str, Any]:
    inv = _inv(a, inv_id)
    if (
        inv.valuation_mode == "UNITS"
        and body.action in (InvestmentAction.BUY, InvestmentAction.SELL)
        and body.units is None
    ):
        raise ValidationFailed("Units are required for unit-based holdings.", code="UNITS_REQUIRED")
    t = InvestmentTransaction(
        owner_id=a.owner_id,
        investment_id=inv.id,
        action=body.action,
        trade_date=body.trade_date,
        units=body.units,
        unit_price=body.unit_price,
        gross_amount=body.gross_amount,
        fees=body.fees,
        transaction_id=body.ledger_transaction_id,
    )
    a.db.add(t)
    if body.ledger_transaction_id:
        svc.link_investment_transaction(
            a.db, a.owner_id, inv, body.ledger_transaction_id, body.action, load_context(a.db, a.owner_id), a.actor
        )
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "investment.transaction", "investment", inv.id, ["transactions"])
    a.commit()
    return svc.holding_summary(a.db, inv, today_in(a.user().timezone))


class ValuationIn(BaseModel):
    valuation_date: date
    total_value: MoneyIn | None = None
    unit_price: MoneyIn | None = None
    source: str = Field(default="MANUAL", max_length=30)


@router.post("/investments/{inv_id}/valuations", status_code=201)
def add_valuation(inv_id: uuid.UUID, body: ValuationIn, a: WriteAuth) -> dict[str, Any]:
    inv = _inv(a, inv_id)
    today = today_in(a.user().timezone)
    if body.valuation_date > today:
        raise ValidationFailed("Valuations cannot be dated in the future.", code="FUTURE_VALUATION")
    total = body.total_value
    if total is None:
        if body.unit_price is None or inv.valuation_mode != "UNITS":
            raise ValidationFailed("Provide a total value (or unit price for unit holdings).", code="VALUE_REQUIRED")
        units = Decimal(svc.holding_summary(a.db, inv, body.valuation_date)["units"] or 0)
        total = (units * body.unit_price).quantize(Decimal("0.01"))
    a.db.add(
        InvestmentValuation(
            owner_id=a.owner_id,
            investment_id=inv.id,
            valuation_date=body.valuation_date,
            unit_price=body.unit_price,
            total_value=total,
            currency=inv.currency,
            source=body.source.upper(),
            is_estimated=False,
        )
    )
    a.db.flush()
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "investment.valuation", "investment", inv.id, ["valuation"])
    a.commit()
    return svc.holding_summary(a.db, inv, today)
