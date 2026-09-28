"""Transfer matching between opposite allocations on different owned accounts.

Amount/date proximity alone only ever produces a *suggestion*. Automatic confirmation needs a
unique candidate pair plus strong identity evidence: the same reliable reference, or one leg's
description identifying the other owned account.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from .enums import AccountKind, PaymentChannel
from .text import last4_mentions, normalize_description, normalize_reference

ALGORITHM_VERSION = "transfer-match-v1"
MAX_DATE_GAP_DAYS = 3
MAX_SUGGESTIONS_PER_LEG = 3

_TRANSFER_WORDS = re.compile(r"\b(SELF|OWN|TRF|TRANSFER|FUND TRANSFER|FT|SWEEP)\b")
_CARD_PAYMENT_WORDS = re.compile(
    r"\b(CC PAYMENT|CREDIT CARD|CARD PAYMENT|PAYMENT RECEIVED|THANK YOU|BILLPAY|CC BILL|AUTOPAY)\b"
)
_LOAN_WORDS = re.compile(r"\b(EMI|LOAN|HOME LOAN|PRINCIPAL)\b")


@dataclass(frozen=True, slots=True)
class Leg:
    allocation_id: UUID
    transaction_id: UUID
    account_id: UUID
    account_kind: AccountKind
    account_last4: str | None
    amount: Decimal
    currency: str
    transaction_date: date
    description: str = ""
    reference: str | None = None
    channel: PaymentChannel = PaymentChannel.OTHER


@dataclass(slots=True)
class Proposal:
    outflow: Leg
    inflow: Leg
    confidence: Decimal
    auto_confirm: bool
    reason: str
    evidence: dict[str, object] = field(default_factory=dict)


def _evidence(out: Leg, inn: Leg) -> dict[str, object]:
    out_desc = normalize_description(out.description)
    in_desc = normalize_description(inn.description)
    ref_out, ref_in = normalize_reference(out.reference), normalize_reference(inn.reference)
    return {
        "same_reference": bool(ref_out and ref_out == ref_in),
        "out_mentions_in_account": bool(inn.account_last4 and inn.account_last4 in last4_mentions(out.description)),
        "in_mentions_out_account": bool(out.account_last4 and out.account_last4 in last4_mentions(inn.description)),
        "transfer_keyword": bool(_TRANSFER_WORDS.search(out_desc) or _TRANSFER_WORDS.search(in_desc)),
        "card_payment": inn.account_kind == AccountKind.CREDIT_CARD
        and bool(_CARD_PAYMENT_WORDS.search(out_desc) or _CARD_PAYMENT_WORDS.search(in_desc)),
        "loan_payment": inn.account_kind == AccountKind.LOAN
        and bool(_LOAN_WORDS.search(out_desc) or _LOAN_WORDS.search(in_desc)),
        "cash_withdrawal": out.channel == PaymentChannel.ATM and inn.account_kind == AccountKind.CASH,
        "date_gap_days": abs((inn.transaction_date - out.transaction_date).days),
    }


def _score(ev: dict[str, object]) -> Decimal:
    score = Decimal("0.50")
    if ev["same_reference"]:
        score += Decimal("0.35")
    if ev["out_mentions_in_account"] or ev["in_mentions_out_account"]:
        score += Decimal("0.25")
    if ev["transfer_keyword"] or ev["card_payment"] or ev["loan_payment"] or ev["cash_withdrawal"]:
        score += Decimal("0.10")
    gap = ev["date_gap_days"]
    assert isinstance(gap, int)
    score -= Decimal("0.05") * gap
    return min(max(score, Decimal("0.05")), Decimal("0.99"))


def _is_strong(ev: dict[str, object]) -> bool:
    return bool(ev["same_reference"] or ev["out_mentions_in_account"] or ev["in_mentions_out_account"])


def match_transfers(legs: list[Leg], rejected_pairs: frozenset[tuple[UUID, UUID]] = frozenset()) -> list[Proposal]:
    """Propose transfer pairs among eligible, currently unmatched legs.

    ``rejected_pairs`` contains ``(outflow_allocation_id, inflow_allocation_id)`` pairs the user
    has rejected; user decisions always win over automatic runs.
    """
    by_key: dict[tuple[str, Decimal], list[Leg]] = defaultdict(list)
    for leg in legs:
        if leg.amount > 0:
            by_key[(leg.currency, leg.amount)].append(leg)

    edges: list[Proposal] = []
    for out in legs:
        if out.amount >= 0:
            continue
        for inn in by_key.get((out.currency, -out.amount), []):
            if inn.account_id == out.account_id or inn.transaction_id == out.transaction_id:
                continue
            if abs((inn.transaction_date - out.transaction_date).days) > MAX_DATE_GAP_DAYS:
                continue
            if (out.allocation_id, inn.allocation_id) in rejected_pairs:
                continue
            ev = _evidence(out, inn)
            edges.append(Proposal(out, inn, _score(ev), False, "amount_date", ev))

    out_degree: dict[UUID, int] = defaultdict(int)
    in_degree: dict[UUID, int] = defaultdict(int)
    for e in edges:
        out_degree[e.outflow.allocation_id] += 1
        in_degree[e.inflow.allocation_id] += 1

    proposals: list[Proposal] = []
    per_leg: dict[UUID, int] = defaultdict(int)
    for e in sorted(edges, key=lambda p: (-p.confidence, p.outflow.transaction_date, str(p.outflow.allocation_id))):
        unique = out_degree[e.outflow.allocation_id] == 1 and in_degree[e.inflow.allocation_id] == 1
        strong = _is_strong(e.evidence)
        e.auto_confirm = unique and strong
        e.reason = (
            "unique_strong_evidence"
            if e.auto_confirm
            else ("unique_weak_evidence" if unique else "ambiguous_candidates")
        )
        if per_leg[e.outflow.allocation_id] >= MAX_SUGGESTIONS_PER_LEG:
            continue
        per_leg[e.outflow.allocation_id] += 1
        proposals.append(e)
    return proposals
