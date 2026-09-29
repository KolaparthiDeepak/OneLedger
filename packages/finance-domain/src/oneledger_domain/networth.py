"""Coverage-aware net worth and balance reconciliation.

Each component (account balance or investment valuation) has exactly one valuation owner.
Missing values make the total *partial*; they are never substituted with zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .enums import AccountNature
from .money import ZERO
from .text import plural

CALCULATION_VERSION = "net-worth-v1"
DEFAULT_STALE_DAYS = 45


@dataclass(frozen=True, slots=True)
class Observation:
    value: Decimal
    as_of: date
    source_id: str


@dataclass(frozen=True, slots=True)
class Component:
    key: str
    label: str
    nature: AccountNature
    currency: str
    kind: str
    observation: Observation | None


def select_latest(observations: list[Observation], cutoff: date) -> Observation | None:
    """Latest observation at or before ``cutoff``; later observations are never back-filled."""
    eligible = [o for o in observations if o.as_of <= cutoff]
    return max(eligible, key=lambda o: (o.as_of, o.source_id)) if eligible else None


@dataclass(slots=True)
class CurrencyTotal:
    currency: str
    assets: Decimal = ZERO
    liabilities: Decimal = ZERO

    @property
    def net_worth(self) -> Decimal:
        return self.assets - self.liabilities


@dataclass(slots=True)
class NetWorth:
    as_of: date
    totals: dict[str, CurrencyTotal] = field(default_factory=dict)
    included: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.missing)

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.missing:
            out.append(
                f"{plural(len(self.missing), 'account or holding', 'accounts or holdings')} with no balance "
                f"or value on or before {self.as_of:%d %b %Y}."
            )
        if self.stale:
            out.append(
                f"{plural(len(self.stale), 'account or holding', 'accounts or holdings')} using a balance "
                f"older than {DEFAULT_STALE_DAYS} days."
            )
        if len(self.totals) > 1:
            out.append("Totals are grouped by currency; no FX conversion is applied.")
        return out


def compute_net_worth(components: list[Component], as_of: date, stale_days: int = DEFAULT_STALE_DAYS) -> NetWorth:
    result = NetWorth(as_of=as_of)
    totals: dict[str, CurrencyTotal] = defaultdict(lambda: CurrencyTotal(""))
    for c in components:
        if c.observation is None:
            result.missing.append(c.key)
            continue
        if c.observation.as_of > as_of:
            raise ValueError("observation after cutoff passed to compute_net_worth")
        t = totals[c.currency]
        t.currency = c.currency
        if c.nature == AccountNature.ASSET:
            t.assets += c.observation.value
        else:
            t.liabilities += c.observation.value
        result.included.append(c.key)
        result.source_ids.append(c.observation.source_id)
        if (as_of - c.observation.as_of).days > stale_days:
            result.stale.append(c.key)
    result.totals = dict(totals)
    return result


@dataclass(frozen=True, slots=True)
class Change:
    available: bool
    reason: str
    delta: dict[str, Decimal]


def compare(current: NetWorth, previous: NetWorth) -> Change:
    """A change is only reported when both points cover the same components completely."""
    if current.partial or previous.partial:
        return Change(False, "partial_coverage", {})
    if set(current.included) != set(previous.included):
        return Change(False, "coverage_changed", {})
    delta = {
        cur: current.totals[cur].net_worth - previous.totals.get(cur, CurrencyTotal(cur)).net_worth
        for cur in current.totals
    }
    return Change(True, "comparable", delta)


# --- Reconciliation -------------------------------------------------------------------------


def balance_change(nature: AccountNature, signed_amount: Decimal) -> Decimal:
    """Natural-balance change for an equity-signed transaction amount."""
    return signed_amount if nature == AccountNature.ASSET else -signed_amount


def expected_closing(nature: AccountNature, opening: Decimal, posted_amounts: list[Decimal]) -> Decimal:
    """Assets: opening + sum(amounts). Liabilities (owed): opening - sum(amounts)."""
    total = sum(posted_amounts, ZERO)
    return opening + total if nature == AccountNature.ASSET else opening - total


@dataclass(frozen=True, slots=True)
class Reconciliation:
    expected: Decimal
    observed: Decimal
    discrepancy: Decimal

    @property
    def balanced(self) -> bool:
        return self.discrepancy == 0


def reconcile(
    nature: AccountNature, opening: Decimal, posted_amounts: list[Decimal], observed: Decimal
) -> Reconciliation:
    exp = expected_closing(nature, opening, posted_amounts)
    return Reconciliation(exp, observed, observed - exp)
