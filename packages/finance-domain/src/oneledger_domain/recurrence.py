"""Recurring transaction detection (salary, rent, EMI, subscriptions, SIP, insurance, bills).

Requires at least three posted occurrences grouped by account + merchant key + direction.
Missed occurrences are forecasts only; nothing here creates ledger entries.
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from .enums import RecurrenceCadence, RecurrenceType
from .periods import add_months
from .text import normalize_description

DETECTION_VERSION = "recurrence-v1"
MIN_OCCURRENCES = 3
FIXED_AMOUNT_TOLERANCE = Decimal("0.10")
VARIABLE_AMOUNT_TOLERANCE = Decimal("0.60")

_CADENCES: list[tuple[RecurrenceCadence, int, int, int]] = [
    # cadence, min interval days, max interval days, day tolerance for next date
    (RecurrenceCadence.WEEKLY, 6, 8, 2),
    (RecurrenceCadence.MONTHLY, 26, 35, 5),
    (RecurrenceCadence.QUARTERLY, 84, 98, 7),
    (RecurrenceCadence.ANNUAL, 350, 380, 10),
]

_TYPE_PATTERNS: list[tuple[re.Pattern[str], RecurrenceType]] = [
    (re.compile(r"\b(SALARY|SAL|PAYROLL)\b"), RecurrenceType.SALARY),
    (re.compile(r"\b(EMI|LOAN|HOME LOAN|NACH)\b"), RecurrenceType.EMI),
    (re.compile(r"\b(SIP|MUTUAL FUND|MF|ZERODHA COIN|GROWW|KUVERA|BSE STAR)\b"), RecurrenceType.SIP),
    (re.compile(r"\b(INSURANCE|LIC|POLICY|PREMIUM|HDFC LIFE|ICICI PRU|STAR HEALTH)\b"), RecurrenceType.INSURANCE),
    (re.compile(r"\bRENT\b"), RecurrenceType.RENT),
    (
        re.compile(
            r"\b(NETFLIX|SPOTIFY|YOUTUBE|PRIME|HOTSTAR|DISNEY|APPLE|GOOGLE|ICLOUD|MICROSOFT|ADOBE|OPENAI|"
            r"ANTHROPIC|CLAUDE|NOTION|ZEE5|SONYLIV|JIOCINEMA|AUDIBLE|LINKEDIN|GITHUB|CHATGPT|SUBSCRIPTION)\b"
        ),
        RecurrenceType.SUBSCRIPTION,
    ),
    (
        re.compile(r"\b(ELECTRICITY|BESCOM|MSEB|TATA POWER|WATER|GAS|BROADBAND|AIRTEL|JIO|VODAFONE|BSNL|ACT)\b"),
        RecurrenceType.BILL,
    ),
]


@dataclass(frozen=True, slots=True)
class Occurrence:
    transaction_id: UUID
    account_id: UUID
    transaction_date: date
    amount: Decimal
    description: str
    merchant: str | None = None
    category_code: str | None = None


@dataclass(frozen=True, slots=True)
class RecurringPattern:
    key: str
    account_id: UUID
    merchant_key: str
    direction: int
    cadence: RecurrenceCadence
    recurrence_type: RecurrenceType
    typical_amount: Decimal
    amount_variable: bool
    occurrences: tuple[UUID, ...]
    last_date: date
    expected_next_date: date
    confidence: Decimal


def merchant_key(description: str, merchant: str | None) -> str:
    if merchant:
        return normalize_description(merchant)
    tokens = [t for t in normalize_description(description).split() if not any(ch.isdigit() for ch in t)]
    stop = {
        "UPI",
        "NEFT",
        "IMPS",
        "RTGS",
        "POS",
        "ECOM",
        "ACH",
        "NACH",
        "DR",
        "CR",
        "TO",
        "FROM",
        "BY",
        "PAYMENT",
        "TRF",
    }
    meaningful = [t for t in tokens if t not in stop and len(t) > 1]
    return " ".join(meaningful[:3]) or "UNKNOWN"


def next_date(last: date, cadence: RecurrenceCadence, anchor_day: int) -> date:
    if cadence == RecurrenceCadence.WEEKLY:
        return last + timedelta(days=7)
    months = {RecurrenceCadence.MONTHLY: 1, RecurrenceCadence.QUARTERLY: 3, RecurrenceCadence.ANNUAL: 12}[cadence]
    nxt = add_months(date(last.year, last.month, 1), months)
    # Keep the anchor day where the month allows it (handles February and month-end).
    return nxt.replace(day=min(anchor_day, _month_len(nxt)))


def _month_len(d: date) -> int:
    return (add_months(date(d.year, d.month, 1), 1) - date(d.year, d.month, 1)).days


def _classify_type(desc: str, category: str | None, direction: int) -> RecurrenceType:
    text = normalize_description(desc)
    for pattern, rtype in _TYPE_PATTERNS:
        if pattern.search(text):
            if rtype == RecurrenceType.SALARY and direction < 0:
                continue
            return rtype
    if category:
        cat = category.upper()
        if "SALARY" in cat and direction > 0:
            return RecurrenceType.SALARY
        if "SUBSCRIPTION" in cat:
            return RecurrenceType.SUBSCRIPTION
        if "INSURANCE" in cat:
            return RecurrenceType.INSURANCE
        if "RENT" in cat:
            return RecurrenceType.RENT
        if "TRANSFER" in cat:
            return RecurrenceType.TRANSFER
    return RecurrenceType.OTHER


def detect_recurring(occurrences: list[Occurrence]) -> list[RecurringPattern]:
    groups: dict[tuple[UUID, str, int], list[Occurrence]] = defaultdict(list)
    for o in occurrences:
        if o.amount == 0:
            continue
        direction = 1 if o.amount > 0 else -1
        groups[(o.account_id, merchant_key(o.description, o.merchant), direction)].append(o)

    patterns: list[RecurringPattern] = []
    for (account_id, mkey, direction), items in groups.items():
        if len(items) < MIN_OCCURRENCES or mkey == "UNKNOWN":
            continue
        items.sort(key=lambda o: o.transaction_date)
        intervals = [(b.transaction_date - a.transaction_date).days for a, b in itertools.pairwise(items)]
        if not intervals or min(intervals) == 0:
            # Same-day repeats are not a cadence; handled by anomaly rules instead.
            intervals = [i for i in intervals if i > 0]
            if len(intervals) < MIN_OCCURRENCES - 1:
                continue
        med = statistics.median(intervals)
        cadence_info = next(((c, lo, hi, tol) for c, lo, hi, tol in _CADENCES if lo <= med <= hi), None)
        if cadence_info is None:
            continue
        cadence, lo, hi, _tol = cadence_info
        regular = sum(1 for i in intervals if lo <= i <= hi) / len(intervals)
        if regular < 0.66:
            continue
        amounts = [abs(o.amount) for o in items]
        typical = Decimal(statistics.median(amounts))
        max_dev = max(abs(a - typical) / typical for a in amounts) if typical else Decimal(1)
        if max_dev > VARIABLE_AMOUNT_TOLERANCE:
            continue
        variable = max_dev > FIXED_AMOUNT_TOLERANCE
        last = items[-1]
        anchor_day = int(statistics.median([o.transaction_date.day for o in items]))
        expected = next_date(last.transaction_date, cadence, anchor_day)
        confidence = Decimal(
            str(round(0.5 + 0.3 * regular + (0.0 if variable else 0.15) + min(len(items), 12) / 240, 2))
        )
        rtype = _classify_type(last.description + " " + (last.merchant or ""), last.category_code, direction)
        patterns.append(
            RecurringPattern(
                key=f"{account_id}:{mkey}:{direction}:{cadence.value}",
                account_id=account_id,
                merchant_key=mkey,
                direction=direction,
                cadence=cadence,
                recurrence_type=rtype,
                typical_amount=typical * direction,
                amount_variable=variable,
                occurrences=tuple(o.transaction_id for o in items),
                last_date=last.transaction_date,
                expected_next_date=expected,
                confidence=min(confidence, Decimal("0.99")),
            )
        )
    return sorted(patterns, key=lambda p: (-abs(p.typical_amount), p.merchant_key))


def is_overdue(pattern_next: date, cadence: RecurrenceCadence, today: date) -> bool:
    tol = next(t for c, _lo, _hi, t in _CADENCES if c == cadence)
    return today > pattern_next + timedelta(days=tol)
