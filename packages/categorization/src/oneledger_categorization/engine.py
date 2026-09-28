"""Hybrid categorization pipeline.

Order: locked user correction -> explicit enabled user rule -> known merchant mapping ->
built-in deterministic patterns -> (optional AI suggestion, handled by the API) -> fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from oneledger_domain.enums import AllocationEffect, ClassificationSource
from oneledger_domain.text import normalize_description

from .patterns import match_builtin
from .rules import RuleSpec, TxnView, rule_matches

ENGINE_VERSION = "categorize-v1"


@dataclass(frozen=True, slots=True)
class UserRule:
    id: UUID
    priority: int
    version: int
    spec: RuleSpec


@dataclass(frozen=True, slots=True)
class MerchantMapping:
    merchant_id: UUID
    name: str
    normalized_name: str
    aliases: tuple[str, ...]
    category_code: str | None
    effect: AllocationEffect | None


@dataclass(frozen=True, slots=True)
class Classification:
    category_code: str | None
    effect: AllocationEffect
    source: ClassificationSource
    confidence: Decimal
    merchant_name: str | None = None
    merchant_id: UUID | None = None
    rule_id: UUID | None = None
    rule_version: int | None = None


FALLBACK = Classification(None, AllocationEffect.UNCLASSIFIED, ClassificationSource.FALLBACK, Decimal("0"))


def _merchant_hit(normalized: str, m: MerchantMapping) -> bool:
    padded = f" {normalized} "
    return any(f" {alias} " in padded for alias in (m.normalized_name, *m.aliases) if alias)


def find_merchant(description: str, merchants: list[MerchantMapping]) -> MerchantMapping | None:
    normalized = normalize_description(description)
    # Longest alias wins so "SWIGGY INSTAMART" beats "SWIGGY".
    best: tuple[int, MerchantMapping] | None = None
    for m in merchants:
        for alias in (m.normalized_name, *m.aliases):
            if alias and f" {alias} " in f" {normalized} " and (best is None or len(alias) > best[0]):
                best = (len(alias), m)
    return best[1] if best else None


def categorize(
    txn: TxnView,
    rules: list[UserRule],
    merchants: list[MerchantMapping],
    category_effects: dict[str, AllocationEffect],
) -> Classification:
    """Deterministic classification. ``category_effects`` maps owner category codes to effects."""
    sign = 1 if txn.amount > 0 else -1
    for rule in sorted(rules, key=lambda r: (r.priority, str(r.id))):
        if rule_matches(rule.spec, txn):
            return Classification(
                rule.spec.category_code,
                rule.spec.effect,
                ClassificationSource.RULE,
                Decimal("1.00"),
                merchant_name=rule.spec.merchant_name,
                rule_id=rule.id,
                rule_version=rule.version,
            )

    merchant = find_merchant(txn.description, merchants)
    if merchant and merchant.category_code:
        effect = merchant.effect or category_effects.get(merchant.category_code, AllocationEffect.EXPENSE)
        # A merchant mapped to an expense category still yields an expense effect for refunds (positive).
        return Classification(
            merchant.category_code,
            effect,
            ClassificationSource.MERCHANT,
            Decimal("0.95"),
            merchant_name=merchant.name,
            merchant_id=merchant.merchant_id,
        )

    normalized = normalize_description(txn.description)
    pattern = match_builtin(normalized, sign)
    if pattern:
        effect = pattern.effect
        if pattern.category in category_effects and pattern.effect not in (AllocationEffect.TRANSFER,):
            effect = category_effects[pattern.category]
        return Classification(
            pattern.category,
            effect,
            ClassificationSource.PATTERN,
            pattern.confidence,
            merchant_name=pattern.merchant or (merchant.name if merchant else None),
            merchant_id=merchant.merchant_id if merchant else None,
        )
    if merchant:
        return Classification(
            None,
            AllocationEffect.UNCLASSIFIED,
            ClassificationSource.FALLBACK,
            Decimal("0"),
            merchant_name=merchant.name,
            merchant_id=merchant.merchant_id,
        )
    return FALLBACK
