"""Cash-flow forecast from confirmed recurring patterns. No statistical confidence is invented."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .enums import RecurrenceCadence
from .money import ZERO
from .recurrence import next_date

FORECAST_VERSION = "forecast-v1"


@dataclass(frozen=True, slots=True)
class Schedule:
    label: str
    amount: Decimal  # signed: + inflow, - outflow
    currency: str
    cadence: RecurrenceCadence
    next_date: date
    anchor_day: int


@dataclass(frozen=True, slots=True)
class ForecastItem:
    label: str
    due_date: date
    amount: Decimal
    currency: str


@dataclass(slots=True)
class Forecast:
    start: date
    end_exclusive: date
    items: list[ForecastItem] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def totals(self) -> dict[str, dict[str, Decimal]]:
        out: dict[str, dict[str, Decimal]] = {}
        for i in self.items:
            t = out.setdefault(i.currency, {"inflow": ZERO, "outflow": ZERO, "net": ZERO})
            if i.amount >= 0:
                t["inflow"] += i.amount
            else:
                t["outflow"] += -i.amount
            t["net"] += i.amount
        return out


def forecast(schedules: list[Schedule], today: date, days: int) -> Forecast:
    end = today + timedelta(days=days)
    fc = Forecast(
        start=today,
        end_exclusive=end,
        assumptions=[
            "Only confirmed recurring items are projected at their typical amount.",
            "One-off and variable spending is not included.",
        ],
    )
    for s in schedules:
        due = s.next_date
        guard = 0
        while due < today and guard < 60:
            due, guard = next_date(due, s.cadence, s.anchor_day), guard + 1
        while due < end and guard < 400:
            fc.items.append(ForecastItem(s.label, due, s.amount, s.currency))
            due, guard = next_date(due, s.cadence, s.anchor_day), guard + 1
    fc.items.sort(key=lambda i: (i.due_date, i.label))
    return fc
