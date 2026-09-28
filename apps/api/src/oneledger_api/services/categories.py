"""Category tree, merchant mappings, rules and the per-request categorization cache."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from oneledger_categorization.categories import seed_categories
from oneledger_categorization.engine import (
    Classification,
    MerchantMapping,
    UserRule,
    categorize,
)
from oneledger_categorization.rules import RuleSpec, TxnView
from oneledger_db.models import TransactionCategory, TransactionCategoryRule, TransactionMerchant
from oneledger_domain.enums import AllocationEffect
from oneledger_domain.text import normalize_description
from sqlalchemy import select
from sqlalchemy.orm import Session


def seed_owner_categories(db: Session, owner_id: uuid.UUID) -> int:
    existing = set(db.scalars(select(TransactionCategory.code).where(TransactionCategory.owner_id == owner_id)))
    ids: dict[str, uuid.UUID] = {
        c.code: c.id for c in db.scalars(select(TransactionCategory).where(TransactionCategory.owner_id == owner_id))
    }
    created = 0
    for seed in seed_categories():
        if seed.code in existing:
            continue
        cat = TransactionCategory(
            id=uuid.uuid4(),
            owner_id=owner_id,
            code=seed.code,
            name=seed.name,
            parent_id=ids.get(seed.parent) if seed.parent else None,
            default_effect=seed.effect,
            is_system=True,
        )
        db.add(cat)
        db.flush()
        ids[seed.code] = cat.id
        created += 1
    return created


@dataclass
class CategorizationContext:
    """Owner categorization inputs loaded once per request/job."""

    categories_by_code: dict[str, TransactionCategory]
    categories_by_id: dict[uuid.UUID, TransactionCategory]
    rules: list[UserRule]
    merchants: list[MerchantMapping]
    effects: dict[str, AllocationEffect] = field(default_factory=dict)

    def classify(self, view: TxnView) -> Classification:
        return categorize(view, self.rules, self.merchants, self.effects)

    def category_id(self, code: str | None) -> uuid.UUID | None:
        if code is None:
            return None
        cat = self.categories_by_code.get(code)
        return cat.id if cat else None

    def descendants(self, category_id: uuid.UUID) -> set[uuid.UUID]:
        out = {category_id}
        changed = True
        while changed:
            changed = False
            for c in self.categories_by_id.values():
                if c.parent_id in out and c.id not in out:
                    out.add(c.id)
                    changed = True
        return out

    def root_of(self, category_id: uuid.UUID | None) -> TransactionCategory | None:
        cur = self.categories_by_id.get(category_id) if category_id else None
        seen = 0
        while cur is not None and cur.parent_id is not None and seen < 20:
            cur = self.categories_by_id.get(cur.parent_id)
            seen += 1
        return cur


def load_context(db: Session, owner_id: uuid.UUID) -> CategorizationContext:
    cats = list(db.scalars(select(TransactionCategory).where(TransactionCategory.owner_id == owner_id)))
    by_code = {c.code: c for c in cats}
    by_id = {c.id: c for c in cats}
    rules: list[UserRule] = []
    for r in db.scalars(
        select(TransactionCategoryRule).where(
            TransactionCategoryRule.owner_id == owner_id,
            TransactionCategoryRule.enabled.is_(True),
            TransactionCategoryRule.deleted_at.is_(None),
        )
    ):
        target = by_id.get(r.target_category_id)
        if target is None:
            continue
        spec = RuleSpec.model_validate({**r.conditions, "category_code": target.code, "effect": r.target_effect})
        rules.append(UserRule(r.id, r.priority, r.version, spec))
    merchants = [
        MerchantMapping(
            m.id,
            m.name,
            m.normalized_name,
            tuple(normalize_description(a) for a in m.aliases),
            by_id[m.default_category_id].code if m.default_category_id in by_id else None,
            by_id[m.default_category_id].default_effect if m.default_category_id in by_id else None,
        )
        for m in db.scalars(select(TransactionMerchant).where(TransactionMerchant.owner_id == owner_id))
    ]
    effects = {c.code: c.default_effect for c in cats}
    return CategorizationContext(by_code, by_id, rules, merchants, effects)
