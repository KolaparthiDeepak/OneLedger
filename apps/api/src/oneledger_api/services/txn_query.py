"""Transaction explorer query: whitelisted filters, stable keyset pagination, evidence reproduction."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    Anomaly,
    RecurringTransactionMember,
    ReviewItem,
    Transaction,
    TransactionAllocation,
    TransactionCategory,
    TransactionCategoryRule,
    TransactionTag,
    TransactionTagLink,
    TransferLeg,
)
from oneledger_domain.enums import AllocationEffect, ReviewStatus, TransactionSourceKind, TransactionStatus
from oneledger_domain.text import normalize_description
from oneledger_shared.errors import ValidationFailed
from sqlalchemy import ColumnElement, Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from .categories import CategorizationContext

MAX_LIMIT = 200
SORTS = {"date_desc", "date_asc", "amount_desc", "amount_asc"}


@dataclass
class TxnFilters:
    start_date: date | None = None
    end_date_exclusive: date | None = None
    account_ids: list[uuid.UUID] = field(default_factory=list)
    category_id: uuid.UUID | None = None
    uncategorized: bool = False
    effect: AllocationEffect | None = None
    merchant: str | None = None
    q: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    direction: str | None = None  # debit | credit
    tag_id: uuid.UUID | None = None
    recurring: bool | None = None
    transfer: bool | None = None
    source: TransactionSourceKind | None = None
    status: TransactionStatus | None = None
    currency: str | None = None
    include_deleted: bool = False
    needs_review: bool | None = None


def filters_from_query(kind: str, params: dict[str, Any]) -> TxnFilters:
    f = TxnFilters()
    if params.get("start"):
        f.start_date = date.fromisoformat(params["start"])
    if params.get("end"):
        f.end_date_exclusive = date.fromisoformat(params["end"])
    f.account_ids = [uuid.UUID(a) for a in params.get("account_ids", [])]
    f.currency = params.get("currency")
    if kind == "category_breakdown" and params.get("effect"):
        f.effect = AllocationEffect(params["effect"])
    if params.get("category_id"):
        f.category_id = uuid.UUID(params["category_id"])
    if params.get("effect_filter"):
        f.effect = AllocationEffect(params["effect_filter"])
    return f


def encode_cursor(values: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(data, dict):
            raise ValueError
        return {str(k): str(v) for k, v in data.items()}
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValidationFailed("Invalid cursor.", code="INVALID_CURSOR") from exc


def build_query(owner_id: uuid.UUID, f: TxnFilters, cat: CategorizationContext) -> Select[tuple[Transaction]]:
    stmt = select(Transaction).where(
        Transaction.owner_id == owner_id, Transaction.merged_into_id.is_(None), Transaction.is_published.is_(True)
    )
    if not f.include_deleted:
        stmt = stmt.where(Transaction.deleted_at.is_(None))
    if f.status:
        stmt = stmt.where(Transaction.status == f.status)
    if f.start_date:
        stmt = stmt.where(Transaction.transaction_date >= f.start_date)
    if f.end_date_exclusive:
        stmt = stmt.where(Transaction.transaction_date < f.end_date_exclusive)
    if f.start_date and f.end_date_exclusive and f.end_date_exclusive <= f.start_date:
        raise ValidationFailed("end_date_exclusive must be after start_date.", code="INVALID_RANGE")
    if f.account_ids:
        stmt = stmt.where(Transaction.account_id.in_(f.account_ids))
    if f.currency:
        stmt = stmt.where(Transaction.currency == f.currency)
    if f.source:
        stmt = stmt.where(Transaction.source == f.source)
    if f.direction == "debit":
        stmt = stmt.where(Transaction.amount < 0)
    elif f.direction == "credit":
        stmt = stmt.where(Transaction.amount > 0)
    if f.min_amount is not None:
        stmt = stmt.where(func.abs(Transaction.amount) >= f.min_amount)
    if f.max_amount is not None:
        stmt = stmt.where(func.abs(Transaction.amount) <= f.max_amount)
    if f.merchant:
        stmt = stmt.where(Transaction.merchant_name.ilike(f"%{_escape_like(f.merchant[:80])}%", escape="\\"))
    if f.q:
        raw = _escape_like(f.q[:100])
        needle = normalize_description(f.q[:100])
        conds: list[ColumnElement[bool]] = [
            Transaction.merchant_name.ilike(f"%{raw}%", escape="\\"),
            Transaction.notes.ilike(f"%{raw}%", escape="\\"),
        ]
        if needle:
            conds.append(Transaction.normalized_description.contains(needle, autoescape=True))
        stmt = stmt.where(or_(*conds))
    alloc = TransactionAllocation
    if f.category_id or f.effect or f.uncategorized:
        alloc_conds: list[ColumnElement[bool]] = [alloc.transaction_id == Transaction.id]
        if f.category_id:
            alloc_conds.append(alloc.category_id.in_(cat.descendants(f.category_id)))
        if f.effect:
            alloc_conds.append(alloc.effect == f.effect)
        if f.uncategorized:
            alloc_conds.append(alloc.effect == AllocationEffect.UNCLASSIFIED)
        stmt = stmt.where(exists(select(alloc.id).where(and_(*alloc_conds))))
    if f.tag_id:
        stmt = stmt.where(
            exists(
                select(TransactionTagLink.id).where(
                    TransactionTagLink.transaction_id == Transaction.id, TransactionTagLink.tag_id == f.tag_id
                )
            )
        )
    if f.recurring is not None:
        rec = exists(
            select(RecurringTransactionMember.id).where(RecurringTransactionMember.transaction_id == Transaction.id)
        )
        stmt = stmt.where(rec if f.recurring else ~rec)
    if f.transfer is not None:
        tr = exists(
            select(TransferLeg.id).where(TransferLeg.transaction_id == Transaction.id, TransferLeg.is_active.is_(True))
        )
        stmt = stmt.where(tr if f.transfer else ~tr)
    if f.needs_review:
        stmt = stmt.where(
            exists(
                select(ReviewItem.id).where(
                    ReviewItem.owner_id == owner_id,
                    ReviewItem.status == ReviewStatus.OPEN,
                    Transaction.id == func.any(ReviewItem.transaction_ids),
                )
            )
        )
    return stmt


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def paginate(
    db: Session, stmt: Select[tuple[Transaction]], sort: str, limit: int, cursor: str | None
) -> tuple[list[Transaction], str | None]:
    if sort not in SORTS:
        raise ValidationFailed("Unsupported sort.", code="INVALID_SORT")
    limit = max(1, min(limit, MAX_LIMIT))
    desc = sort.endswith("_desc")
    key = Transaction.transaction_date if sort.startswith("date") else Transaction.amount
    if cursor:
        c = decode_cursor(cursor)
        kval: Any = date.fromisoformat(c["k"]) if sort.startswith("date") else Decimal(c["k"])
        cid = uuid.UUID(c["id"])
        stmt = stmt.where(
            or_(key < kval, and_(key == kval, Transaction.id < cid))
            if desc
            else or_(key > kval, and_(key == kval, Transaction.id > cid))
        )
    stmt = stmt.order_by(key.desc() if desc else key.asc(), Transaction.id.desc() if desc else Transaction.id.asc())
    rows = list(db.scalars(stmt.limit(limit + 1)))
    nxt = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        kv = last.transaction_date.isoformat() if sort.startswith("date") else str(last.amount)
        nxt = encode_cursor({"k": kv, "id": str(last.id)})
    return rows, nxt


def serialize(
    db: Session, owner_id: uuid.UUID, txns: list[Transaction], cat: CategorizationContext, *, detail: bool
) -> list[dict[str, Any]]:
    """Batch-load related rows. Without read-detail scope, raw descriptions/references are withheld."""
    if not txns:
        return []
    ids = [t.id for t in txns]
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({t.account_id for t in txns})))}
    allocs: dict[uuid.UUID, list[TransactionAllocation]] = {}
    for a in db.scalars(
        select(TransactionAllocation)
        .where(TransactionAllocation.transaction_id.in_(ids))
        .order_by(TransactionAllocation.position)
    ):
        allocs.setdefault(a.transaction_id, []).append(a)
    legs = {
        leg.allocation_id: leg
        for leg in db.scalars(
            select(TransferLeg).where(TransferLeg.transaction_id.in_(ids), TransferLeg.is_active.is_(True))
        )
    }
    tags: dict[uuid.UUID, list[dict[str, str]]] = {}
    for link, tag in db.execute(
        select(TransactionTagLink, TransactionTag)
        .join(TransactionTag, TransactionTag.id == TransactionTagLink.tag_id)
        .where(TransactionTagLink.transaction_id.in_(ids))
    ):
        tags.setdefault(link.transaction_id, []).append({"id": str(tag.id), "name": tag.name})
    recurring = set(
        db.scalars(
            select(RecurringTransactionMember.transaction_id).where(RecurringTransactionMember.transaction_id.in_(ids))
        )
    )
    anomalies = set(
        db.scalars(
            select(Anomaly.transaction_id).where(Anomaly.transaction_id.in_(ids), Anomaly.dismissed_at.is_(None))
        )
    )
    review_ids: set[uuid.UUID] = set()
    for item in db.scalars(
        select(ReviewItem).where(
            ReviewItem.owner_id == owner_id,
            ReviewItem.status == ReviewStatus.OPEN,
            ReviewItem.transaction_ids.overlap(ids),
        )
    ):
        review_ids |= set(item.transaction_ids)
    rule_ids = {a.rule_id for lst in allocs.values() for a in lst if a.rule_id}
    rule_names = (
        dict(
            db.execute(
                select(TransactionCategoryRule.id, TransactionCategoryRule.name).where(
                    TransactionCategoryRule.id.in_(rule_ids)
                )
            )
            .tuples()
            .all()
        )
        if rule_ids
        else {}
    )
    cats: dict[uuid.UUID, TransactionCategory] = cat.categories_by_id
    out = []
    for t in txns:
        acct = accounts.get(t.account_id)
        t_allocs = allocs.get(t.id, [])
        items = []
        for a in t_allocs:
            c = cats.get(a.category_id) if a.category_id else None
            root = cat.root_of(a.category_id) if a.category_id else None
            leg = legs.get(a.id)
            items.append(
                {
                    "id": str(a.id),
                    "amount": str(a.amount),
                    "effect": a.effect.value,
                    "category": {
                        "id": str(c.id),
                        "code": c.code,
                        "name": c.name,
                        "parent_name": root.name if root and root.id != c.id else None,
                    }
                    if c
                    else None,
                    "classification_source": a.classification_source.value,
                    "confidence": str(a.classification_confidence) if a.classification_confidence is not None else None,
                    "locked": a.is_locked,
                    # Why this category: the rule or AI model that chose it (shown to the owner).
                    "rule": rule_names.get(a.rule_id) if a.rule_id else None,
                    "model": a.model,
                    "note": a.note,
                    "transfer_id": str(leg.transfer_id) if leg else None,
                }
            )
        first_cat = t_allocs[0].category_id if t_allocs else None
        label = t.merchant_name or (cats[first_cat].name if first_cat in cats else "Transaction")
        out.append(
            {
                "id": str(t.id),
                "account": {
                    "id": str(t.account_id),
                    "name": acct.name if acct else None,
                    "kind": acct.kind.value if acct else None,
                },
                "amount": str(t.amount),
                "currency": t.currency,
                "direction": "credit" if t.amount > 0 else "debit",
                "transaction_date": t.transaction_date.isoformat(),
                "value_date": t.value_date.isoformat() if t.value_date else None,
                "status": t.status.value,
                "channel": t.payment_channel.value,
                "source": t.source.value,
                "description": t.raw_description if detail else label,
                "merchant": t.merchant_name,
                "reference": t.reference if detail else None,
                "notes": t.notes if detail else None,
                "allocations": items,
                "is_split": len(items) > 1,
                "is_transfer": any(i["transfer_id"] for i in items),
                "tags": tags.get(t.id, []),
                "recurring": t.id in recurring,
                "anomaly": t.id in anomalies,
                "needs_review": t.id in review_ids,
                "deleted": t.deleted_at is not None,
                "revision": t.revision,
                "version": t.version,
            }
        )
    return out
