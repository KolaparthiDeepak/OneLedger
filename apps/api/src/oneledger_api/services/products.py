"""Loans, credit cards and investments on top of the unified ledger."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    BalanceSnapshot,
    CreditCard,
    CreditCardStatement,
    Investment,
    InvestmentTransaction,
    InvestmentValuation,
    Loan,
    LoanPayment,
    LoanRateChange,
    Transaction,
    TransactionAllocation,
)
from oneledger_domain import loans as ln
from oneledger_domain.enums import (
    AccountKind,
    AllocationEffect,
    BalanceKind,
    BalanceSource,
    InvestmentAction,
    TransactionSourceKind,
)
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import audit, bump_ledger_revision
from .categories import CategorizationContext
from .ledger import (
    NewTransaction,
    SplitPart,
    allocations_of,
    create_transaction,
    get_account,
    get_transaction,
    split_transaction,
)
from .reports import account_balances, active_txn_filter
from .transfers import confirm_pair

# --- Loans ---------------------------------------------------------------------------------------


def rate_str(rate: Decimal) -> str:
    """Interest rates without storage padding: 8.500000 -> "8.5"."""
    return format(Decimal(rate).normalize(), "f")


def get_loan(db: Session, owner_id: uuid.UUID, loan_id: uuid.UUID) -> Loan:
    loan = db.get(Loan, loan_id)
    if loan is None or loan.owner_id != owner_id or loan.deleted_at is not None:
        raise NotFoundError()
    return loan


def current_rate(db: Session, loan: Loan, on: date) -> Decimal:
    rate = db.scalars(
        select(LoanRateChange.annual_rate_percent)
        .where(LoanRateChange.loan_id == loan.id, LoanRateChange.effective_date <= on)
        .order_by(LoanRateChange.effective_date.desc())
    ).first()
    if rate is None:
        rate = db.scalars(
            select(LoanRateChange.annual_rate_percent)
            .where(LoanRateChange.loan_id == loan.id)
            .order_by(LoanRateChange.effective_date)
        ).first()
    if rate is None:
        raise ValidationFailed("The loan has no interest rate.", code="RATE_MISSING")
    return Decimal(rate)


def loan_summary(db: Session, owner_id: uuid.UUID, loan: Loan, today: date) -> dict[str, Any]:
    account = db.get(Account, loan.account_id)
    assert account is not None
    bal = account_balances(db, owner_id, today, [account])[0]
    paid = db.execute(
        select(
            func.coalesce(func.sum(LoanPayment.principal), 0),
            func.coalesce(func.sum(LoanPayment.interest), 0),
            func.coalesce(func.sum(LoanPayment.fees), 0),
            func.coalesce(func.sum(LoanPayment.prepayment), 0),
            func.count(),
            func.bool_and(LoanPayment.is_actual),
        ).where(LoanPayment.loan_id == loan.id, LoanPayment.deleted_at.is_(None))
    ).one()
    principal_paid, interest_paid, fees_paid, prepaid, n_payments, all_actual = paid
    rate = current_rate(db, loan, today)
    projection: dict[str, Any] | None = None
    outstanding = bal.balance
    if outstanding is not None and outstanding > 0:
        paid_count = int(n_payments)
        remaining_term = max(loan.tenure_months - paid_count, 1)
        next_due = _next_emi(loan, today)
        try:
            sched = ln.build_schedule(
                outstanding,
                rate,
                remaining_term,
                next_due,
                emi=loan.emi_amount,
                rate_changes=_future_changes(db, loan, today),
            )
            projection = {
                "remaining_emis": len(sched.rows),
                "projected_interest": str(sched.total_interest),
                "projected_payoff_date": sched.payoff_date,
                "next_emi_date": next_due,
                "assumptions": [*sched.assumptions, "Projection only; lender statements are authoritative."],
            }
        except ln.LoanModelError as exc:
            projection = {"error": str(exc)}
    return {
        "id": str(loan.id),
        "account_id": str(loan.account_id),
        "lender": loan.lender,
        "loan_type": loan.loan_type,
        "currency": loan.currency,
        "original_principal": str(loan.original_principal),
        "outstanding_principal": str(outstanding) if outstanding is not None else None,
        "outstanding_as_of": bal.derived_through,
        "outstanding_source": "observed_balance" if outstanding is not None else None,
        "interest_rate_percent": rate_str(rate),
        "emi_amount": str(loan.emi_amount),
        "emi_day": loan.emi_day,
        "tenure_months": loan.tenure_months,
        "start_date": loan.start_date,
        "first_emi_date": loan.first_emi_date,
        "principal_paid": str(principal_paid),
        "interest_paid": str(interest_paid),
        "fees_paid": str(fees_paid),
        "prepayments": str(prepaid),
        "payments_recorded": int(n_payments),
        "components_all_actual": bool(all_actual) if n_payments else None,
        "projection": projection,
        "version": loan.version,
    }


def _next_emi(loan: Loan, today: date) -> date:
    from oneledger_domain.periods import add_months

    d = loan.first_emi_date
    guard = 0
    while d <= today and guard < 1200:
        guard += 1
        d = add_months(loan.first_emi_date, guard)
    return d


def _future_changes(db: Session, loan: Loan, today: date) -> list[ln.RateChange]:
    return [
        ln.RateChange(r.effective_date, Decimal(r.annual_rate_percent))
        for r in db.scalars(
            select(LoanRateChange).where(LoanRateChange.loan_id == loan.id, LoanRateChange.effective_date > today)
        )
    ]


def create_loan(
    db: Session,
    owner_id: uuid.UUID,
    *,
    lender: str,
    loan_type: str,
    currency: str,
    original_principal: Decimal,
    opening_outstanding: Decimal,
    opening_date: date,
    start_date: date,
    first_emi_date: date,
    tenure_months: int,
    annual_rate: Decimal,
    emi_amount: Decimal | None,
    actor: str,
) -> Loan:
    if original_principal <= 0 or opening_outstanding < 0:
        raise ValidationFailed("Principal must be positive and outstanding non-negative.", code="INVALID_PRINCIPAL")
    try:
        emi = emi_amount or ln.compute_emi(original_principal, annual_rate, tenure_months)
        # Validate the model can represent the loan (rejects negative amortization).
        if opening_outstanding > 0:
            ln.build_schedule(opening_outstanding, annual_rate, tenure_months, first_emi_date, emi=emi)
    except ln.LoanModelError as exc:
        raise ValidationFailed(str(exc), code="LOAN_UNSUPPORTED") from exc
    account = Account(
        owner_id=owner_id,
        name=f"{lender} {loan_type}".strip()[:120],
        kind=AccountKind.LOAN,
        currency=currency,
        opening_date=opening_date,
    )
    db.add(account)
    db.flush()
    db.add(
        BalanceSnapshot(
            owner_id=owner_id,
            account_id=account.id,
            amount=opening_outstanding,
            currency=currency,
            balance_kind=BalanceKind.OPENING,
            as_of=opening_date,
            source=BalanceSource.MANUAL,
            is_observed=True,
            source_ref="loan_opening",
        )
    )
    loan = Loan(
        owner_id=owner_id,
        account_id=account.id,
        lender=lender,
        loan_type=loan_type,
        original_principal=original_principal,
        opening_outstanding=opening_outstanding,
        opening_date=opening_date,
        start_date=start_date,
        first_emi_date=first_emi_date,
        tenure_months=tenure_months,
        emi_amount=emi,
        emi_day=first_emi_date.day,
        currency=currency,
    )
    db.add(loan)
    db.flush()
    db.add(
        LoanRateChange(
            owner_id=owner_id,
            loan_id=loan.id,
            annual_rate_percent=annual_rate,
            effective_date=start_date,
            source="MANUAL",
        )
    )
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "loan.create", "loan", loan.id, ["principal", "rate", "tenure"])
    return loan


def record_loan_payment(
    db: Session,
    owner_id: uuid.UUID,
    loan: Loan,
    cat: CategorizationContext,
    *,
    transaction_id: uuid.UUID,
    principal: Decimal | None,
    interest: Decimal | None,
    fees: Decimal,
    is_prepayment: bool,
    actor: str,
) -> LoanPayment:
    """Split the bank EMI debit into principal / interest / fees and link principal to the loan account.

    Lender-reported components take precedence; without them the split is an *estimate* at the
    current observed outstanding principal and is labelled as such.
    """
    txn = get_transaction(db, owner_id, transaction_id, lock=True)
    if txn.amount >= 0:
        raise ValidationFailed("Select the debit that paid the loan.", code="PAYMENT_DIRECTION")
    if txn.currency != loan.currency:
        raise ValidationFailed("Currency mismatch.", code="CURRENCY_MISMATCH")
    if db.scalars(
        select(LoanPayment).where(LoanPayment.transaction_id == txn.id, LoanPayment.deleted_at.is_(None))
    ).first():
        raise ConflictError("This payment is already recorded.", code="PAYMENT_EXISTS")
    total = -txn.amount
    is_actual = principal is not None and interest is not None
    if is_prepayment:
        principal, interest, fees = total - fees, Decimal(0), fees
        is_actual = True
    elif not is_actual:
        account = db.get(Account, loan.account_id)
        assert account is not None
        outstanding = account_balances(db, owner_id, txn.transaction_date, [account])[0].balance
        if outstanding is None:
            raise ValidationFailed(
                "Add the loan's outstanding balance first, or enter the components.", code="OUTSTANDING_UNKNOWN"
            )
        try:
            principal, interest = ln.split_emi(outstanding, current_rate(db, loan, txn.transaction_date), total - fees)
        except ln.LoanModelError as exc:
            raise ValidationFailed(str(exc), code="LOAN_UNSUPPORTED") from exc
    assert principal is not None and interest is not None
    if principal < 0 or interest < 0 or fees < 0 or principal + interest + fees != total:
        raise ValidationFailed("Principal + interest + fees must equal the payment amount.", code="COMPONENT_MISMATCH")
    parts = (
        [
            SplitPart(
                -principal,
                AllocationEffect.LOAN_PRINCIPAL,
                cat.category_id("LOANS_PRINCIPAL"),
                "Loan principal",
                loan_id=loan.id,
            )
        ]
        if principal
        else []
    )
    if interest:
        parts.append(
            SplitPart(
                -interest, AllocationEffect.EXPENSE, cat.category_id("LOANS_INTEREST"), "Loan interest", loan_id=loan.id
            )
        )
    if fees:
        parts.append(SplitPart(-fees, AllocationEffect.EXPENSE, cat.category_id("FEES"), "Loan fees", loan_id=loan.id))
    allocs = split_transaction(db, owner_id, txn, parts, actor, cat)
    principal_alloc = next((a for a in allocs if a.effect == AllocationEffect.LOAN_PRINCIPAL), None)
    if principal_alloc is not None:
        loan_account = get_account(db, owner_id, loan.account_id)
        # Reuse an existing loan-account credit for the same amount/date when source data has it.
        counterpart = (
            db.execute(
                select(TransactionAllocation)
                .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
                .where(
                    Transaction.owner_id == owner_id,
                    Transaction.account_id == loan_account.id,
                    active_txn_filter(),
                    Transaction.amount == principal,
                    Transaction.transaction_date >= txn.transaction_date,
                    Transaction.transaction_date <= txn.transaction_date,
                )
            )
            .scalars()
            .first()
        )
        if counterpart is None:
            derived = create_transaction(
                db,
                owner_id,
                NewTransaction(
                    account=loan_account,
                    amount=principal,
                    transaction_date=txn.transaction_date,
                    description=f"Principal repayment ({'prepayment' if is_prepayment else 'EMI'})",
                    source=TransactionSourceKind.MANUAL_DERIVED,
                    provider_namespace="derived",
                ),
                cat,
                actor,
            )
            counterpart = allocations_of(db, derived.id)[0]
        confirm_pair(
            db, owner_id, principal_alloc.id, counterpart.id, cat, actor=actor, method="MANUAL", reason="loan_payment"
        )
    payment = LoanPayment(
        owner_id=owner_id,
        loan_id=loan.id,
        payment_date=txn.transaction_date,
        transaction_id=txn.id,
        principal=principal if not is_prepayment else Decimal(0),
        interest=interest,
        fees=fees,
        prepayment=principal if is_prepayment else Decimal(0),
        is_actual=is_actual,
        source="USER" if is_actual else "ESTIMATE",
    )
    db.add(payment)
    db.flush()
    audit(db, owner_id, actor, "loan.payment", "loan", loan.id, ["payments"])
    return payment


# --- Credit cards --------------------------------------------------------------------------------


def card_summary(db: Session, owner_id: uuid.UUID, card: CreditCard, today: date) -> dict[str, Any]:
    account = db.get(Account, card.account_id)
    assert account is not None
    bal = account_balances(db, owner_id, today, [account])[0]
    stmt = db.scalars(
        select(CreditCardStatement)
        .where(CreditCardStatement.card_id == card.id)
        .order_by(CreditCardStatement.period_end.desc())
    ).first()
    latest = statement_status(db, owner_id, card, stmt, today) if stmt else None
    available = (card.credit_limit - bal.balance) if card.credit_limit is not None and bal.balance is not None else None
    return {
        "id": str(card.id),
        "account_id": str(card.account_id),
        "name": account.name,
        "issuer": card.issuer,
        "network": card.network,
        "masked_identifier": account.masked_identifier,
        "currency": account.currency,
        "outstanding": str(bal.balance) if bal.balance is not None else None,
        "outstanding_as_of": bal.derived_through,
        "credit_limit": str(card.credit_limit) if card.credit_limit is not None else None,
        "available_credit": str(available) if available is not None else None,
        "statement_day": card.statement_day,
        "payment_due_days": card.payment_due_days,
        "latest_statement": latest,
        "version": card.version,
    }


def statement_status(
    db: Session, owner_id: uuid.UUID, card: CreditCard, stmt: CreditCardStatement, today: date
) -> dict[str, Any]:
    paid = Decimal(
        db.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.owner_id == owner_id,
                Transaction.account_id == card.account_id,
                active_txn_filter(),
                Transaction.amount > 0,
                Transaction.transaction_date > stmt.period_end,
                Transaction.transaction_date <= max(stmt.due_date, today),
            )
        )
        or 0
    )
    if stmt.statement_balance <= 0 or paid >= stmt.statement_balance:
        status = "PAID"
    elif stmt.minimum_due is not None and paid >= stmt.minimum_due:
        status = "MINIMUM_PAID" if today <= stmt.due_date else "PARTIAL_OVERDUE"
    elif today > stmt.due_date:
        status = "OVERDUE"
    else:
        status = "PARTIAL" if paid > 0 else "DUE"
    return {
        "id": str(stmt.id),
        "period_start": stmt.period_start,
        "period_end": stmt.period_end,
        "statement_balance": str(stmt.statement_balance),
        "minimum_due": str(stmt.minimum_due) if stmt.minimum_due is not None else None,
        "due_date": stmt.due_date,
        "paid_since_statement": str(paid),
        "status": status,
    }


# --- Investments ---------------------------------------------------------------------------------


def holding_summary(db: Session, inv: Investment, today: date) -> dict[str, Any]:
    txs = list(
        db.scalars(
            select(InvestmentTransaction).where(
                InvestmentTransaction.investment_id == inv.id, InvestmentTransaction.deleted_at.is_(None)
            )
        )
    )
    units = Decimal(0)
    contributed = withdrawn = income = fees = Decimal(0)
    cost_known = inv.valuation_mode == "UNITS"
    for t in txs:
        fees += t.fees
        if t.action in (InvestmentAction.BUY, InvestmentAction.CONTRIBUTION):
            contributed += t.gross_amount
            if t.units is not None:
                units += t.units
            elif inv.valuation_mode == "UNITS":
                cost_known = False
        elif t.action in (InvestmentAction.SELL, InvestmentAction.WITHDRAWAL):
            withdrawn += t.gross_amount
            if t.units is not None:
                units -= t.units
            cost_known = False  # realized-gain lot matching is not modelled; gains become unknown
        elif t.action in (InvestmentAction.DIVIDEND, InvestmentAction.INTEREST):
            income += t.gross_amount
    v = db.scalars(
        select(InvestmentValuation)
        .where(
            InvestmentValuation.investment_id == inv.id,
            InvestmentValuation.deleted_at.is_(None),
            InvestmentValuation.valuation_date <= today,
        )
        .order_by(InvestmentValuation.valuation_date.desc(), InvestmentValuation.created_at.desc())
    ).first()
    value = v.total_value if v else None
    gain = (value - (contributed + fees)) if (value is not None and cost_known and txs) else None
    return {
        "id": str(inv.id),
        "name": inv.name,
        "instrument_type": inv.instrument_type.value,
        "identifier": inv.identifier,
        "currency": inv.currency,
        "valuation_mode": inv.valuation_mode,
        "account_id": str(inv.account_id) if inv.account_id else None,
        "units": str(units) if inv.valuation_mode == "UNITS" else None,
        "net_contributions": str(contributed - withdrawn),
        "contributions": str(contributed),
        "withdrawals": str(withdrawn),
        "income": str(income),
        "fees": str(fees),
        "value": str(value) if value is not None else None,
        "valued_on": v.valuation_date if v else None,
        "valuation_source": v.source if v else None,
        "value_is_estimated": v.is_estimated if v else None,
        "unrealized_gain": str(gain) if gain is not None else None,
        "gain_note": None if gain is not None else "Unknown: needs a valuation and complete purchase cost.",
        "include_in_net_worth": inv.include_in_net_worth,
        "version": inv.version,
    }


def link_investment_transaction(
    db: Session,
    owner_id: uuid.UUID,
    inv: Investment,
    txn_id: uuid.UUID,
    action: InvestmentAction,
    cat: CategorizationContext,
    actor: str,
) -> None:
    """Classify the funding ledger movement as an investment (not consumption expense)."""
    txn = get_transaction(db, owner_id, txn_id, lock=True)
    allocs = allocations_of(db, txn.id, lock=True)
    if len(allocs) != 1:
        raise ConflictError("Select a single (unsplit) movement to link.", code="SPLIT_EXISTS")
    a = allocs[0]
    if action in (InvestmentAction.DIVIDEND, InvestmentAction.INTEREST):
        a.effect, a.category_id = AllocationEffect.INCOME, cat.category_id("INCOME_DIVIDEND")
    elif action == InvestmentAction.FEE:
        a.effect, a.category_id = AllocationEffect.EXPENSE, cat.category_id("FEES")
    else:
        a.effect, a.category_id = AllocationEffect.INVESTMENT, cat.category_id("INVESTMENTS")
    a.investment_id = inv.id
    a.is_locked = True
    from oneledger_domain.enums import ClassificationSource

    a.classification_source = ClassificationSource.USER
    txn.version += 1
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "investment.link", "transaction", txn.id, ["effect", "investment_id"])
