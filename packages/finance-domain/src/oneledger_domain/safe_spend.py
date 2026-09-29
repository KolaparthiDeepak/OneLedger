"""Safe to spend: how much you can spend per day until your next income without missing a bill.

    safe total = known liquid balance (bank, cash, wallets) - obligations due before the next income
    per day    = safe total / days until the next income (at least 1)

Obligations are bills, EMIs, SIPs, rent and card bills expected in that window. Nothing is invented:
the balance must be known, every obligation is listed, and the result says what it assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from .enums import RecurrenceCadence
from .periods import add_months

SAFE_SPEND_VERSION = "safe-spend-v1"
DEFAULT_HORIZON_DAYS = 31


@dataclass(frozen=True, slots=True)
class Obligation:
    label: str
    due_date: date
    amount: Decimal  # positive: money that will leave
    kind: str  # recurring | card | loan
    source_id: str


@dataclass(frozen=True, slots=True)
class SafeToSpend:
    liquid_balance: Decimal
    until: date
    days_left: int
    obligations: list[Obligation]
    obligations_total: Decimal
    safe_total: Decimal
    per_day: Decimal
    assumptions: list[str] = field(default_factory=list)

    @property
    def shortfall(self) -> bool:
        return self.safe_total < 0


def roll_forward(expected: date, cadence: RecurrenceCadence, today: date) -> date:
    """The first expected date strictly after today (a late payment is assumed to follow its cadence)."""
    d = expected
    guard = 0
    while d <= today and guard < 400:
        guard += 1
        if cadence == RecurrenceCadence.WEEKLY:
            d = d + timedelta(days=7)
        elif cadence == RecurrenceCadence.QUARTERLY:
            d = add_months(d, 3)
        elif cadence == RecurrenceCadence.ANNUAL:
            d = add_months(d, 12)
        else:
            d = add_months(d, 1)
    return d


def compute(
    liquid_balance: Decimal,
    today: date,
    next_income: date | None,
    obligations: list[Obligation],
    assumptions: list[str] | None = None,
) -> SafeToSpend:
    notes = list(assumptions or [])
    if next_income is None or next_income <= today:
        until = today + timedelta(days=DEFAULT_HORIZON_DAYS)
        notes.append(f"No regular income found, so this looks {DEFAULT_HORIZON_DAYS} days ahead.")
    else:
        until = next_income
    window = sorted((o for o in obligations if today <= o.due_date < until), key=lambda o: (o.due_date, o.label))
    total = sum((o.amount for o in window), Decimal(0))
    days_left = max((until - today).days, 1)
    safe = liquid_balance - total
    per_day = (safe / days_left).quantize(Decimal("0.01"), rounding=ROUND_DOWN) if safe > 0 else Decimal(0)
    return SafeToSpend(liquid_balance, until, days_left, window, total, safe, per_day, notes)
