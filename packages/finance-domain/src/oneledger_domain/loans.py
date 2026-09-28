"""Fixed-period monthly reducing-balance loan estimates.

This is an *estimate* for standard EMI loans: ``r = annual_rate / 12``;
``EMI = P*r*(1+r)^n / ((1+r)^n - 1)`` or ``P/n`` when ``r = 0``. Daily-accrual, moratorium and
fee-bearing loans differ; lender-reported components always take precedence over this model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum

from .money import ZERO
from .periods import add_months

CALCULATION_VERSION = "loan-schedule-v1"
MAX_PERIODS = 1200
_CENT = Decimal("0.01")


class LoanModelError(ValueError):
    """Inputs are invalid or describe a loan this model does not support."""


class PrepaymentStrategy(StrEnum):
    REDUCE_TENURE = "REDUCE_TENURE"
    REDUCE_EMI = "REDUCE_EMI"


class RateChangePolicy(StrEnum):
    KEEP_EMI = "KEEP_EMI"
    KEEP_TENURE = "KEEP_TENURE"


@dataclass(frozen=True, slots=True)
class RateChange:
    effective_date: date
    annual_rate_percent: Decimal


@dataclass(frozen=True, slots=True)
class Prepayment:
    payment_date: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    period: int
    due_date: date
    opening_principal: Decimal
    rate_percent: Decimal
    emi: Decimal
    interest: Decimal
    principal: Decimal
    prepayment: Decimal
    closing_principal: Decimal


@dataclass(slots=True)
class Schedule:
    rows: list[ScheduleRow] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @property
    def total_interest(self) -> Decimal:
        return sum((r.interest for r in self.rows), ZERO)

    @property
    def total_principal(self) -> Decimal:
        return sum((r.principal + r.prepayment for r in self.rows), ZERO)

    @property
    def total_paid(self) -> Decimal:
        return sum((r.emi + r.prepayment for r in self.rows), ZERO)

    @property
    def payoff_date(self) -> date | None:
        return self.rows[-1].due_date if self.rows else None


def _r(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


def monthly_rate(annual_rate_percent: Decimal) -> Decimal:
    return annual_rate_percent / Decimal(1200)


def compute_emi(principal: Decimal, annual_rate_percent: Decimal, months: int) -> Decimal:
    if principal <= 0:
        raise LoanModelError("principal must be positive")
    if months <= 0:
        raise LoanModelError("term must be a positive number of months")
    if annual_rate_percent < 0:
        raise LoanModelError("negative interest rates are not supported")
    with localcontext() as ctx:
        ctx.prec = 40
        r = monthly_rate(annual_rate_percent)
        if r == 0:
            return _r(principal / months)
        growth = (1 + r) ** months
        return _r(principal * r * growth / (growth - 1))


def build_schedule(
    principal: Decimal,
    annual_rate_percent: Decimal,
    months: int,
    first_due_date: date,
    *,
    emi: Decimal | None = None,
    rate_changes: list[RateChange] | None = None,
    prepayments: list[Prepayment] | None = None,
    prepayment_strategy: PrepaymentStrategy = PrepaymentStrategy.REDUCE_TENURE,
    rate_change_policy: RateChangePolicy = RateChangePolicy.KEEP_EMI,
) -> Schedule:
    """Project a monthly schedule from ``principal`` outstanding before ``first_due_date``."""
    if months <= 0 or months > MAX_PERIODS:
        raise LoanModelError(f"term must be between 1 and {MAX_PERIODS} months")
    current_emi = emi if emi is not None else compute_emi(principal, annual_rate_percent, months)
    if current_emi <= 0:
        raise LoanModelError("EMI must be positive")
    changes = sorted(rate_changes or [], key=lambda c: c.effective_date)
    pre = sorted(prepayments or [], key=lambda p: p.payment_date)
    schedule = Schedule(
        assumptions=[
            "Monthly reducing balance with rate/12 per period.",
            "Interest rounded to 0.01 each period; final payment adjusted to clear principal.",
            f"Rate changes: {rate_change_policy.value}; prepayments: {prepayment_strategy.value}.",
        ]
    )
    rate = annual_rate_percent
    outstanding = principal
    remaining_planned = months
    period = 0
    with localcontext() as ctx:
        ctx.prec = 40
        while outstanding > 0:
            period += 1
            if period > MAX_PERIODS:
                raise LoanModelError("schedule does not converge within the maximum term")
            due = add_months(first_due_date, period - 1)
            # Apply effective rate changes up to this due date.
            while changes and changes[0].effective_date <= due:
                change = changes.pop(0)
                if change.annual_rate_percent < 0:
                    raise LoanModelError("negative interest rates are not supported")
                rate = change.annual_rate_percent
                if rate_change_policy == RateChangePolicy.KEEP_TENURE:
                    current_emi = compute_emi(outstanding, rate, max(remaining_planned, 1))
            interest = _r(outstanding * monthly_rate(rate))
            if interest > 0 and current_emi <= interest:
                raise LoanModelError("EMI does not cover interest (negative amortization is not supported)")
            payment = current_emi
            principal_part = payment - interest
            residual = outstanding - principal_part
            # Final payment clears principal: either the EMI covers it, or only rounding residue remains.
            if principal_part >= outstanding or (remaining_planned <= 1 and residual <= _CENT * months):
                principal_part = outstanding
                payment = outstanding + interest
            closing = outstanding - principal_part
            prepay_total = ZERO
            # Prepayments dated on/before this due date are applied after this period's EMI.
            while pre and pre[0].payment_date <= due:
                p = pre.pop(0)
                if p.amount <= 0:
                    raise LoanModelError("prepayment must be positive")
                applied = min(p.amount, closing)
                prepay_total += applied
                closing -= applied
            schedule.rows.append(
                ScheduleRow(period, due, outstanding, rate, payment, interest, principal_part, prepay_total, closing)
            )
            remaining_planned -= 1
            if prepay_total > 0 and closing > 0 and prepayment_strategy == PrepaymentStrategy.REDUCE_EMI:
                current_emi = compute_emi(closing, rate, max(remaining_planned, 1))
            outstanding = closing
    return schedule


@dataclass(frozen=True, slots=True)
class PrepaymentImpact:
    baseline_interest: Decimal
    scenario_interest: Decimal
    interest_saved: Decimal
    baseline_payments: int
    scenario_payments: int
    months_saved: int
    baseline_emi: Decimal
    scenario_final_emi: Decimal
    strategy: PrepaymentStrategy


def simulate_prepayment(
    principal: Decimal,
    annual_rate_percent: Decimal,
    months: int,
    first_due_date: date,
    prepayment: Prepayment,
    strategy: PrepaymentStrategy = PrepaymentStrategy.REDUCE_TENURE,
    emi: Decimal | None = None,
) -> PrepaymentImpact:
    """Compare a baseline schedule with one including a prepayment. Never changes actual data."""
    base = build_schedule(principal, annual_rate_percent, months, first_due_date, emi=emi)
    scen = build_schedule(
        principal,
        annual_rate_percent,
        months,
        first_due_date,
        emi=emi,
        prepayments=[prepayment],
        prepayment_strategy=strategy,
    )
    return PrepaymentImpact(
        baseline_interest=base.total_interest,
        scenario_interest=scen.total_interest,
        interest_saved=base.total_interest - scen.total_interest,
        baseline_payments=len(base.rows),
        scenario_payments=len(scen.rows),
        months_saved=len(base.rows) - len(scen.rows),
        baseline_emi=base.rows[0].emi,
        scenario_final_emi=scen.rows[-1].emi if len(scen.rows) < 2 else scen.rows[-2].emi,
        strategy=strategy,
    )


def split_emi(outstanding: Decimal, annual_rate_percent: Decimal, emi_amount: Decimal) -> tuple[Decimal, Decimal]:
    """Estimated (principal, interest) split of one EMI at the current outstanding principal."""
    interest = _r(outstanding * monthly_rate(annual_rate_percent))
    if emi_amount <= interest:
        raise LoanModelError("EMI does not cover interest (negative amortization is not supported)")
    principal = min(emi_amount - interest, outstanding)
    return principal, emi_amount - principal
