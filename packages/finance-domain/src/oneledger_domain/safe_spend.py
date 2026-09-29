"""Safe to spend: how much you can spend per day until your next income without missing a bill.

    safe total = known liquid balance (bank, cash, wallets) - obligations due before the next income
                 (and in the few days after it, in case the income is late)
    per day    = safe total / days until the next income (at least 1)

Obligations are bills, EMIs, SIPs, rent and card bills expected in that window. Nothing is invented:
the balance must be known, every obligation is listed, and the result says what it assumed. Income
that is overdue is not counted on: the window then looks a month ahead instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

from .enums import RecurrenceCadence
from .periods import add_months

SAFE_SPEND_VERSION = "safe-spend-v2"
DEFAULT_HORIZON_DAYS = 31
# Bills due this many days after payday are still reserved: salaries arrive late, rent is due soon after.
AFTER_INCOME_DAYS = 5
# Income more than this many days past its expected date is treated as not arriving.
INCOME_GRACE_DAYS = 3


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


def next_income_date(expected: date, cadence: RecurrenceCadence, today: date) -> date | None:
    """When regular income should next arrive, or None when it is overdue and can't be counted on.

    Income a few days late is still expected on its usual schedule; beyond ``INCOME_GRACE_DAYS`` it is
    overdue, and rolling it forward would promise money that has not come.
    """
    if expected < today - timedelta(days=INCOME_GRACE_DAYS):
        return None
    return roll_forward(expected, cadence, today)


def compute(
    liquid_balance: Decimal,
    today: date,
    next_income: date | None,
    obligations: list[Obligation],
    assumptions: list[str] | None = None,
    *,
    no_income_note: str | None = None,
) -> SafeToSpend:
    notes = list(assumptions or [])
    if next_income is None or next_income <= today:
        until = today + timedelta(days=DEFAULT_HORIZON_DAYS)
        reserve_until = until
        notes.append(no_income_note or f"No regular income found, so this looks {DEFAULT_HORIZON_DAYS} days ahead.")
    else:
        until = next_income
        reserve_until = next_income + timedelta(days=AFTER_INCOME_DAYS)
    window = sorted(
        (o for o in obligations if today <= o.due_date < reserve_until), key=lambda o: (o.due_date, o.label)
    )
    if any(o.due_date >= until for o in window):
        notes.append(f"Bills due up to {AFTER_INCOME_DAYS} days after payday are kept aside in case it is late.")
    total = sum((o.amount for o in window), Decimal(0))
    days_left = max((until - today).days, 1)
    safe = liquid_balance - total
    per_day = (safe / days_left).quantize(Decimal("0.01"), rounding=ROUND_DOWN) if safe > 0 else Decimal(0)
    return SafeToSpend(liquid_balance, until, days_left, window, total, safe, per_day, notes)
