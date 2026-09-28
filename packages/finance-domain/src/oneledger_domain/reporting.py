"""Reporting definitions (implementation plan section 5.3).

Effects drive reporting; categories only drive presentation. SQL queries in the API use these
same definitions through ``summarize`` or through the effect sets exported here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from .enums import AllocationEffect
from .money import ZERO

CALCULATION_VERSION = "summary-v1"

REPORTABLE_TRANSACTION_STATUSES = ("POSTED",)


@dataclass(frozen=True, slots=True)
class AllocationFact:
    effect: AllocationEffect
    amount: Decimal  # signed contribution to account equity


@dataclass(frozen=True, slots=True)
class Summary:
    income: Decimal
    net_expenses: Decimal
    gross_expenses: Decimal
    refunds: Decimal
    unclassified_outflow: Decimal
    unclassified_inflow: Decimal
    unclassified_count: int
    investment_contributions: Decimal
    investment_withdrawals: Decimal
    loan_principal_paid: Decimal
    transfers_in: Decimal
    transfers_out: Decimal

    @property
    def savings(self) -> Decimal:
        """Income minus net expenses; provisional when unclassified allocations exist."""
        return self.income - self.net_expenses

    @property
    def provisional(self) -> bool:
        return self.unclassified_count > 0


def summarize(facts: Iterable[AllocationFact]) -> Summary:
    income = gross = refunds = u_out = u_in = inv_in = inv_out = loan = t_in = t_out = ZERO
    u_count = 0
    for f in facts:
        a = f.amount
        match f.effect:
            case AllocationEffect.INCOME:
                income += a
            case AllocationEffect.EXPENSE:
                if a < 0:
                    gross += -a
                else:
                    refunds += a
            case AllocationEffect.UNCLASSIFIED:
                u_count += 1
                if a < 0:
                    u_out += -a
                else:
                    u_in += a
            case AllocationEffect.INVESTMENT:
                if a < 0:
                    inv_in += -a
                else:
                    inv_out += a
            case AllocationEffect.LOAN_PRINCIPAL:
                if a < 0:
                    loan += -a
            case AllocationEffect.TRANSFER:
                if a < 0:
                    t_out += -a
                else:
                    t_in += a
            case AllocationEffect.ADJUSTMENT:
                pass
    return Summary(
        income=income,
        net_expenses=gross - refunds,
        gross_expenses=gross,
        refunds=refunds,
        unclassified_outflow=u_out,
        unclassified_inflow=u_in,
        unclassified_count=u_count,
        investment_contributions=inv_in,
        investment_withdrawals=inv_out,
        loan_principal_paid=loan,
        transfers_in=t_in,
        transfers_out=t_out,
    )
