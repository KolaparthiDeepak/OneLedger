"""Categories, rules (with dry-run), merchant mappings and tags."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from oneledger_categorization.rules import Condition, RuleSpec, TxnView, rule_matches
from oneledger_db.models import (
    Transaction,
    TransactionAllocation,
    TransactionCategory,
    TransactionCategoryRule,
    TransactionMerchant,
    TransactionTag,
    TransactionTagLink,
)
from oneledger_domain.enums import AllocationEffect
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..deps import ReadAuth, WriteAuth
from ..services.audit import audit
from ..services.categories import load_context
from ..services.ledger import recategorize, upsert_merchant
from ..services.reports import active_txn_filter

router = APIRouter(tags=["categories"])


def cat_out(c: TransactionCategory) -> dict[str, object]:
    return {
        "id": str(c.id),
        "code": c.code,
        "name": c.name,
        "parent_id": str(c.parent_id) if c.parent_id else None,
        "effect": c.default_effect.value,
        "is_system": c.is_system,
        "archived": c.archived_at is not None,
    }


@router.get("/categories")
def list_categories(a: ReadAuth) -> list[dict[str, object]]:
    rows = a.db.scalars(
        select(TransactionCategory).where(TransactionCategory.owner_id == a.owner_id).order_by(TransactionCategory.name)
    ).all()
    return [cat_out(c) for c in rows]


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: uuid.UUID | None = None
    effect: AllocationEffect = AllocationEffect.EXPENSE


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, a: WriteAuth) -> dict[str, object]:
    if body.parent_id:
        parent = a.db.get(TransactionCategory, body.parent_id)
        if parent is None or parent.owner_id != a.owner_id:
            raise NotFoundError("Parent category not found.")
    base = re.sub(r"[^A-Z0-9]+", "_", body.name.upper()).strip("_")[:40] or "CUSTOM"
    code = f"CUSTOM_{base}"
    n = 1
    while a.db.scalars(
        select(TransactionCategory.id).where(
            TransactionCategory.owner_id == a.owner_id, TransactionCategory.code == code
        )
    ).first():
        n += 1
        code = f"CUSTOM_{base}_{n}"
    c = TransactionCategory(
        owner_id=a.owner_id, code=code, name=body.name.strip(), parent_id=body.parent_id, default_effect=body.effect
    )
    a.db.add(c)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "category.create", "category", c.id, ["name"])
    a.commit()
    return cat_out(c)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: uuid.UUID | None = None
    archived: bool | None = None


@router.patch("/categories/{category_id}")
def update_category(category_id: uuid.UUID, body: CategoryPatch, a: WriteAuth) -> dict[str, object]:
    c = a.db.get(TransactionCategory, category_id)
    if c is None or c.owner_id != a.owner_id:
        raise NotFoundError()
    changed = []
    if body.name is not None:
        c.name = body.name.strip()
        changed.append("name")
    if body.parent_id is not None and not c.is_system:
        c.parent_id = body.parent_id
        changed.append("parent_id")
    if body.archived is not None:
        # Archived categories stay attached to history; they are hidden from pickers.
        c.archived_at = datetime.now(UTC) if body.archived else None
        changed.append("archived_at")
    audit(a.db, a.owner_id, a.actor, "category.update", "category", c.id, changed)
    a.commit()
    return cat_out(c)


# --- Rules -------------------------------------------------------------------------------------


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    conditions: list[Condition] = Field(min_length=1, max_length=10)
    category_id: uuid.UUID
    effect: AllocationEffect | None = None
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True


def _rule_out(r: TransactionCategoryRule) -> dict[str, object]:
    return {
        "id": str(r.id),
        "name": r.name,
        "enabled": r.enabled,
        "priority": r.priority,
        "conditions": r.conditions.get("conditions", []),
        "category_id": str(r.target_category_id),
        "effect": r.target_effect.value,
        "version": r.version,
    }


def _spec(a: WriteAuth | ReadAuth, body: RuleIn) -> tuple[RuleSpec, TransactionCategory]:
    cat = a.db.get(TransactionCategory, body.category_id)
    if cat is None or cat.owner_id != a.owner_id:
        raise NotFoundError("Category not found.")
    spec = RuleSpec(conditions=body.conditions, category_code=cat.code, effect=body.effect or cat.default_effect)
    return spec, cat


def _matches(
    a: WriteAuth | ReadAuth, spec: RuleSpec, limit: int
) -> tuple[list[tuple[Transaction, TransactionAllocation]], int, int]:
    rows = a.db.execute(
        select(Transaction, TransactionAllocation)
        .join(TransactionAllocation, TransactionAllocation.transaction_id == Transaction.id)
        .where(Transaction.owner_id == a.owner_id, active_txn_filter())
        .order_by(Transaction.transaction_date.desc())
        .limit(20000)
    ).all()
    matched: list[tuple[Transaction, TransactionAllocation]] = []
    locked = total = 0
    for t, alloc in rows:
        view = TxnView(
            account_id=t.account_id,
            amount=t.amount,
            description=t.raw_description,
            merchant=t.merchant_name,
            channel=t.payment_channel,
        )
        if rule_matches(spec, view):
            total += 1
            if alloc.is_locked:
                locked += 1
            elif len(matched) < limit:
                matched.append((t, alloc))
    return matched, total, locked


@router.get("/rules")
def list_rules(a: ReadAuth) -> list[dict[str, object]]:
    rows = a.db.scalars(
        select(TransactionCategoryRule)
        .where(TransactionCategoryRule.owner_id == a.owner_id, TransactionCategoryRule.deleted_at.is_(None))
        .order_by(TransactionCategoryRule.priority, TransactionCategoryRule.id)
    ).all()
    return [_rule_out(r) for r in rows]


@router.post("/rules/dry-run")
def dry_run(body: RuleIn, a: WriteAuth) -> dict[str, object]:
    """Preview which transactions a rule would affect. Performs no writes."""
    spec, cat = _spec(a, body)
    matched, total, locked = _matches(a, spec, 50)
    a.db.rollback()
    return {
        "matching_transactions": total,
        "locked_skipped": locked,
        "category": cat.name,
        "effect": spec.effect.value,
        "sample": [
            {
                "id": str(t.id),
                "date": t.transaction_date,
                "amount": str(t.amount),
                "description": t.raw_description,
                "current_category_id": str(al.category_id) if al.category_id else None,
                "current_effect": al.effect.value,
            }
            for t, al in matched
        ],
    }


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, a: WriteAuth) -> dict[str, object]:
    spec, cat = _spec(a, body)
    r = TransactionCategoryRule(
        owner_id=a.owner_id,
        name=body.name,
        enabled=body.enabled,
        priority=body.priority,
        conditions={"conditions": [c.model_dump(mode="json") for c in spec.conditions]},
        target_category_id=cat.id,
        target_effect=spec.effect,
    )
    a.db.add(r)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "rule.create", "rule", r.id, ["conditions"])
    a.commit()
    return _rule_out(r)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: uuid.UUID, body: RuleIn, a: WriteAuth) -> dict[str, object]:
    r = a.db.get(TransactionCategoryRule, rule_id)
    if r is None or r.owner_id != a.owner_id or r.deleted_at is not None:
        raise NotFoundError()
    spec, cat = _spec(a, body)
    r.name, r.enabled, r.priority = body.name, body.enabled, body.priority
    r.conditions = {"conditions": [c.model_dump(mode="json") for c in spec.conditions]}
    r.target_category_id, r.target_effect = cat.id, spec.effect
    r.version += 1
    audit(a.db, a.owner_id, a.actor, "rule.update", "rule", r.id, ["conditions"])
    a.commit()
    return _rule_out(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, a: WriteAuth) -> None:
    r = a.db.get(TransactionCategoryRule, rule_id)
    if r is None or r.owner_id != a.owner_id:
        raise NotFoundError()
    r.deleted_at = datetime.now(UTC)
    r.enabled = False
    audit(a.db, a.owner_id, a.actor, "rule.delete", "rule", r.id, ["deleted_at"])
    a.commit()


class ApplyIn(BaseModel):
    confirm: bool = Field(description="Must be true: applying re-categorizes matching unlocked transactions.")


@router.post("/rules/{rule_id}/apply")
def apply_rule(rule_id: uuid.UUID, body: ApplyIn, a: WriteAuth) -> dict[str, int]:
    if not body.confirm:
        raise ValidationFailed("Confirm after reviewing the dry-run.", code="CONFIRMATION_REQUIRED")
    r = a.db.get(TransactionCategoryRule, rule_id)
    if r is None or r.owner_id != a.owner_id or r.deleted_at is not None or not r.enabled:
        raise NotFoundError()
    cat = load_context(a.db, a.owner_id)
    target = cat.categories_by_id[r.target_category_id]
    spec = RuleSpec.model_validate({**r.conditions, "category_code": target.code, "effect": r.target_effect})
    matched, _total, locked = _matches(a, spec, 100_000)
    changed = recategorize(a.db, a.owner_id, [t for t, _ in matched], cat)
    audit(a.db, a.owner_id, a.actor, "rule.apply", "rule", r.id, ["allocations"])
    a.commit()
    return {"evaluated": len(matched), "changed": changed, "locked_skipped": locked}


@router.post("/categories/recategorize")
def recategorize_all(a: WriteAuth) -> dict[str, int]:
    """Re-apply rules, merchants and built-in patterns to every unlocked, unsplit transaction."""
    cat = load_context(a.db, a.owner_id)
    txns = list(a.db.scalars(select(Transaction).where(Transaction.owner_id == a.owner_id, active_txn_filter())))
    changed = recategorize(a.db, a.owner_id, txns, cat)
    audit(a.db, a.owner_id, a.actor, "categories.recategorize", "category", None, ["allocations"])
    a.commit()
    return {"checked": len(txns), "changed": changed}


# --- Merchants & tags --------------------------------------------------------------------------


class MerchantIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    category_id: uuid.UUID | None = None


@router.get("/merchants")
def list_merchants(a: ReadAuth) -> list[dict[str, object]]:
    rows = a.db.scalars(
        select(TransactionMerchant).where(TransactionMerchant.owner_id == a.owner_id).order_by(TransactionMerchant.name)
    ).all()
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "aliases": m.aliases,
            "category_id": str(m.default_category_id) if m.default_category_id else None,
        }
        for m in rows
    ]


@router.post("/merchants", status_code=201)
def create_merchant(body: MerchantIn, a: WriteAuth) -> dict[str, object]:
    if body.category_id:
        c = a.db.get(TransactionCategory, body.category_id)
        if c is None or c.owner_id != a.owner_id:
            raise NotFoundError("Category not found.")
    m = upsert_merchant(a.db, a.owner_id, body.name, body.category_id, body.aliases)
    audit(a.db, a.owner_id, a.actor, "merchant.upsert", "merchant", m.id, ["name", "category"])
    a.commit()
    return {
        "id": str(m.id),
        "name": m.name,
        "aliases": m.aliases,
        "category_id": str(m.default_category_id) if m.default_category_id else None,
    }


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


@router.get("/tags")
def list_tags(a: ReadAuth) -> list[dict[str, object]]:
    rows = a.db.scalars(
        select(TransactionTag).where(TransactionTag.owner_id == a.owner_id).order_by(TransactionTag.name)
    )
    return [{"id": str(t.id), "name": t.name, "color": t.color} for t in rows]


@router.post("/tags", status_code=201)
def create_tag(body: TagIn, a: WriteAuth) -> dict[str, object]:
    name = body.name.strip()
    if a.db.scalars(
        select(TransactionTag).where(TransactionTag.owner_id == a.owner_id, TransactionTag.name == name)
    ).first():
        raise ConflictError("A tag with that name exists.", code="TAG_EXISTS")
    t = TransactionTag(owner_id=a.owner_id, name=name, color=body.color)
    a.db.add(t)
    a.db.flush()
    a.commit()
    return {"id": str(t.id), "name": t.name, "color": t.color}


@router.patch("/tags/{tag_id}")
def rename_tag(tag_id: uuid.UUID, body: TagIn, a: WriteAuth) -> dict[str, object]:
    t = a.db.get(TransactionTag, tag_id)
    if t is None or t.owner_id != a.owner_id:
        raise NotFoundError()
    name = body.name.strip()
    clash = a.db.scalars(
        select(TransactionTag).where(
            TransactionTag.owner_id == a.owner_id, TransactionTag.name == name, TransactionTag.id != t.id
        )
    ).first()
    if clash:
        raise ConflictError("A tag with that name exists.", code="TAG_EXISTS")
    t.name, t.color = name, body.color or t.color
    audit(a.db, a.owner_id, a.actor, "tag.update", "tag", t.id, ["name"])
    a.commit()
    return {"id": str(t.id), "name": t.name, "color": t.color}


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: uuid.UUID, a: WriteAuth) -> None:
    """Remove a tag from every transaction and delete it. Transactions themselves are unchanged."""
    t = a.db.get(TransactionTag, tag_id)
    if t is None or t.owner_id != a.owner_id:
        raise NotFoundError()
    a.db.execute(delete(TransactionTagLink).where(TransactionTagLink.tag_id == t.id))
    a.db.delete(t)
    audit(a.db, a.owner_id, a.actor, "tag.delete", "tag", tag_id, ["name"])
    a.commit()
