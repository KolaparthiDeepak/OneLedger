"""Conservative, lossless duplicate detection.

Identity priority (see implementation plan section 7.2):

1. Same import file replay -- handled by the ingestion service before this module.
2. Stable provider transaction ID -- handled by the ingestion service (unique source index).
3. Same reliable reference + account/currency/amount + compatible date -> auto link when unique.
4. Versioned fingerprint with multiplicity preserved; running balance agreement upgrades a
   fingerprint match to an automatic link, otherwise the pair is flagged for review.

Nothing here ever collapses rows across different accounts or drops identical genuine rows.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .text import normalize_description, normalize_reference

FINGERPRINT_VERSION = 1
REFERENCE_DATE_TOLERANCE_DAYS = 3


def fingerprint(
    account_id: UUID | str,
    transaction_date: date,
    amount: Decimal,
    currency: str,
    description: str | None,
    reference: str | None,
) -> str:
    """Deterministic fingerprint. Excludes provider name and mutable annotations."""
    parts = [
        f"v{FINGERPRINT_VERSION}",
        str(account_id),
        transaction_date.isoformat(),
        f"{amount.normalize():f}",
        currency.upper(),
        normalize_description(description),
        normalize_reference(reference) or "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class Decision(StrEnum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"  # auto link to an existing ledger movement
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"  # needs user review


@dataclass(frozen=True, slots=True)
class Candidate:
    key: int  # caller-defined row identifier
    transaction_date: date
    amount: Decimal
    currency: str
    description: str
    reference: str | None = None
    balance_after: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Existing:
    transaction_id: UUID
    transaction_date: date
    amount: Decimal
    currency: str
    description: str
    reference: str | None = None
    balance_after: Decimal | None = None


@dataclass(slots=True)
class Match:
    decision: Decision
    transaction_id: UUID | None = None
    reason: str = ""
    evidence: dict[str, str] = field(default_factory=dict)


def detect_duplicates(
    account_id: UUID | str, candidates: list[Candidate], existing: list[Existing]
) -> dict[int, Match]:
    """Classify candidates for a single account against existing ledger movements.

    ``existing`` must already be restricted to the same account and a date window around the
    candidates. Each existing movement is claimed by at most one candidate.
    """
    result: dict[int, Match] = {}
    claimed: set[UUID] = set()

    # Step 3: reliable reference matches.
    by_ref: dict[str, list[Existing]] = defaultdict(list)
    for e in existing:
        ref = normalize_reference(e.reference)
        if ref:
            by_ref[ref].append(e)
    for c in candidates:
        ref = normalize_reference(c.reference)
        if not ref or ref not in by_ref:
            continue
        options = [
            e
            for e in by_ref[ref]
            if e.transaction_id not in claimed
            and e.currency == c.currency
            and e.amount == c.amount
            and abs((e.transaction_date - c.transaction_date).days) <= REFERENCE_DATE_TOLERANCE_DAYS
        ]
        if len(options) == 1:
            claimed.add(options[0].transaction_id)
            result[c.key] = Match(Decision.DUPLICATE, options[0].transaction_id, "reference_match", {"reference": ref})
        elif len(options) > 1:
            result[c.key] = Match(
                Decision.POSSIBLE_DUPLICATE, options[0].transaction_id, "ambiguous_reference", {"reference": ref}
            )

    # Step 4: fingerprint with multiplicity. Pair remaining candidates and existing rows
    # one-to-one in stable order; surplus candidates are genuinely new rows.
    fp_existing: dict[str, list[Existing]] = defaultdict(list)
    for e in sorted(existing, key=lambda x: (x.transaction_date, str(x.transaction_id))):
        if e.transaction_id in claimed:
            continue
        fp_existing[
            fingerprint(account_id, e.transaction_date, e.amount, e.currency, e.description, e.reference)
        ].append(e)

    for c in sorted(candidates, key=lambda x: (x.transaction_date, x.key)):
        if c.key in result:
            continue
        fp = fingerprint(account_id, c.transaction_date, c.amount, c.currency, c.description, c.reference)
        pool = fp_existing.get(fp)
        if not pool:
            result[c.key] = Match(Decision.NEW, reason="no_match")
            continue
        # Prefer an existing row whose running balance agrees.
        chosen = None
        if c.balance_after is not None:
            chosen = next((e for e in pool if e.balance_after is not None and e.balance_after == c.balance_after), None)
        if chosen is not None:
            pool.remove(chosen)
            result[c.key] = Match(Decision.DUPLICATE, chosen.transaction_id, "fingerprint_and_balance")
        else:
            e = pool.pop(0)
            result[c.key] = Match(Decision.POSSIBLE_DUPLICATE, e.transaction_id, "fingerprint_only")
    return result


def within_file_multiplicity(candidates: list[Candidate], account_id: UUID | str) -> dict[str, int]:
    """Count identical rows within one file; identical rows remain separate candidates."""
    counts: dict[str, int] = defaultdict(int)
    for c in candidates:
        counts[fingerprint(account_id, c.transaction_date, c.amount, c.currency, c.description, c.reference)] += 1
    return dict(counts)
