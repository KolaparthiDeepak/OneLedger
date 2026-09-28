"""Transparent anomaly rules. These flag items for attention; they are not fraud detection."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

RULES_VERSION = "anomaly-rules-v1"
MIN_HISTORY = 6
MAD_SCALE = Decimal("1.4826")
_PENALTY = re.compile(r"\b(PENALTY|LATE FEE|LATE PAYMENT|OVERLIMIT|BOUNCE|RETURN CHARGES|INSUFFICIENT)\b")


@dataclass(frozen=True, slots=True)
class Spend:
    transaction_id: UUID
    transaction_date: date
    amount: Decimal  # positive magnitude of an expense
    category_code: str | None
    merchant_key: str
    description: str


@dataclass(frozen=True, slots=True)
class Anomaly:
    rule: str
    transaction_id: UUID
    reason: str
    evidence: dict[str, str] = field(default_factory=dict)


def _median(values: list[Decimal]) -> Decimal:
    return Decimal(statistics.median(values))


def detect_anomalies(history: list[Spend], candidates: list[Spend]) -> list[Anomaly]:
    """Evaluate ``candidates`` against prior ``history`` (which may include the candidates)."""
    out: list[Anomaly] = []
    by_category: dict[str, list[Spend]] = defaultdict(list)
    merchants_seen: dict[str, date] = {}
    for s in sorted(history, key=lambda x: x.transaction_date):
        by_category[s.category_code or "UNCATEGORIZED"].append(s)
        merchants_seen.setdefault(s.merchant_key, s.transaction_date)
    all_amounts = sorted(s.amount for s in history)

    for c in candidates:
        cat = c.category_code or "UNCATEGORIZED"
        prior = [s.amount for s in by_category[cat] if s.transaction_date < c.transaction_date]
        if len(prior) >= MIN_HISTORY:
            med = _median(prior)
            mad = _median([abs(a - med) for a in prior]) * MAD_SCALE
            threshold = med + max(Decimal(3) * mad, med)
            if c.amount > threshold:
                out.append(
                    Anomaly(
                        "large_for_category",
                        c.transaction_id,
                        "Amount is well above your usual spending in this category.",
                        {
                            "category": cat,
                            "median": f"{med:.2f}",
                            "threshold": f"{threshold:.2f}",
                            "samples": str(len(prior)),
                        },
                    )
                )
        if len(all_amounts) >= 20 and merchants_seen.get(c.merchant_key) == c.transaction_date:
            p90 = all_amounts[int(len(all_amounts) * 0.9) - 1]
            if c.amount >= p90:
                out.append(
                    Anomaly(
                        "new_large_merchant",
                        c.transaction_id,
                        "First payment to this merchant and larger than 90% of your expenses.",
                        {"p90": f"{p90:.2f}"},
                    )
                )
        if _PENALTY.search(c.description.upper()):
            out.append(Anomaly("penalty_fee", c.transaction_id, "Description indicates a penalty or late fee.", {}))

    # Possible repeat charges: same merchant and amount within one day.
    seen: dict[tuple[str, Decimal], list[Spend]] = defaultdict(list)
    for c in sorted(candidates, key=lambda x: x.transaction_date):
        key = (c.merchant_key, c.amount)
        for prev in seen[key]:
            if 0 <= (c.transaction_date - prev.transaction_date).days <= 1 and c.merchant_key != "UNKNOWN":
                out.append(
                    Anomaly(
                        "possible_repeat_charge",
                        c.transaction_id,
                        "Same merchant and amount charged again within a day.",
                        {"other_transaction_id": str(prev.transaction_id)},
                    )
                )
                break
        seen[key].append(c)
    return out
