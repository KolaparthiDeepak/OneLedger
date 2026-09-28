"""Transaction explorer and ledger edits."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from oneledger_db.models import (
    Import,
    ImportRow,
    TransactionRelation,
    TransactionRevision,
    TransactionSource,
    TransactionTag,
    TransactionTagLink,
)
from oneledger_domain.enums import AllocationEffect, TransactionSourceKind, TransactionStatus
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select

from ..deps import DetailAuth, MoneyIn, ReadAuth, WriteAuth
from ..security.auth import SCOPE_READ_DETAIL
from ..services.audit import audit
from ..services.categories import load_context
from ..services.idempotency import idempotent
from ..services.ledger import (
    NewTransaction,
    SplitPart,
    active_leg_allocation_ids,
    allocations_of,
    correct_transaction,
    create_transaction,
    get_account,
    get_transaction,
    restore,
    set_classification,
    soft_delete,
    split_transaction,
    unlock_classification,
    upsert_merchant,
)
from ..services.reports import load_query
from ..services.transfers import confirm_pair, run_matching
from ..services.txn_query import TxnFilters, build_query, filters_from_query, paginate, serialize

router = APIRouter(tags=["transactions"])


@router.get("/transactions")
def list_transactions(
    a: ReadAuth,
    start_date: date | None = None,
    end_date_exclusive: date | None = None,
    account_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    category_id: uuid.UUID | None = None,
    uncategorized: bool = False,
    effect: AllocationEffect | None = None,
    merchant: Annotated[str | None, Query(max_length=80)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    min_amount: Annotated[str | None, Query(pattern=r"^\d{1,15}(\.\d{1,2})?$")] = None,
    max_amount: Annotated[str | None, Query(pattern=r"^\d{1,15}(\.\d{1,2})?$")] = None,
    direction: Annotated[str | None, Query(pattern="^(debit|credit)$")] = None,
    tag_id: uuid.UUID | None = None,
    recurring: bool | None = None,
    transfer: bool | None = None,
    source: TransactionSourceKind | None = None,
    status: TransactionStatus | None = None,
    needs_review: bool | None = None,
    include_deleted: bool = False,
    query_id: uuid.UUID | None = None,
    sort: str = "date_desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
) -> dict[str, Any]:
    from decimal import Decimal

    cat = load_context(a.db, a.owner_id)
    evidence = None
    if query_id:
        rq = load_query(a.db, a.owner_id, query_id)
        if rq is None:
            raise NotFoundError("Evidence query not found.")
        f = filters_from_query(rq.kind, rq.params)
        f.status = TransactionStatus.POSTED
        from ..services.audit import current_ledger_revision

        now_rev = current_ledger_revision(a.db, a.owner_id)
        evidence = {
            "query_id": str(rq.id),
            "kind": rq.kind,
            "params": rq.params,
            "ledger_revision": rq.ledger_revision,
            "ledger_changed_since": now_rev != rq.ledger_revision,
        }
    else:
        f = TxnFilters(
            start_date=start_date,
            end_date_exclusive=end_date_exclusive,
            account_ids=account_id or [],
            category_id=category_id,
            uncategorized=uncategorized,
            effect=effect,
            merchant=merchant,
            q=q,
            min_amount=Decimal(min_amount) if min_amount else None,
            max_amount=Decimal(max_amount) if max_amount else None,
            direction=direction,
            tag_id=tag_id,
            recurring=recurring,
            transfer=transfer,
            source=source,
            status=status,
            include_deleted=include_deleted,
            needs_review=needs_review,
        )
    rows, nxt = paginate(a.db, build_query(a.owner_id, f, cat), sort, limit, cursor)
    detail = SCOPE_READ_DETAIL in a.principal.scopes
    return {"items": serialize(a.db, a.owner_id, rows, cat, detail=detail), "next_cursor": nxt, "evidence": evidence}


@router.get("/transactions/{txn_id}")
def get_one(txn_id: uuid.UUID, a: DetailAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id)
    item = serialize(a.db, a.owner_id, [t], cat, detail=True)[0]
    sources = list(a.db.scalars(select(TransactionSource).where(TransactionSource.transaction_id == t.id)))
    item["sources"] = [
        {
            "provider": s.provider_namespace,
            "import_row_id": str(s.import_row_id) if s.import_row_id else None,
            "has_provider_id": s.source_transaction_id is not None,
            "active": s.is_active,
            "created_at": s.created_at,
        }
        for s in sources
    ]
    item["revisions"] = [
        {
            "revision": r.revision,
            "reason": r.reason,
            "actor": r.actor,
            "created_at": r.created_at,
            "before": r.before,
            "after": r.after,
        }
        for r in a.db.scalars(
            select(TransactionRevision)
            .where(TransactionRevision.transaction_id == t.id)
            .order_by(TransactionRevision.revision)
        )
    ]
    item["relations"] = [
        {
            "id": str(r.id),
            "kind": r.kind.value,
            "from": str(r.from_transaction_id),
            "to": str(r.to_transaction_id),
            "amount": str(r.amount) if r.amount is not None else None,
            "undone": r.undone_at is not None,
        }
        for r in a.db.scalars(
            select(TransactionRelation).where(
                or_(TransactionRelation.from_transaction_id == t.id, TransactionRelation.to_transaction_id == t.id)
            )
        )
    ]
    item["merged_into_id"] = str(t.merged_into_id) if t.merged_into_id else None
    item["origin"] = statement_origin(a, [s.import_row_id for s in sources if s.import_row_id and s.is_active])
    return item


def statement_origin(a: DetailAuth, row_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """The statement rows this transaction came from, exactly as the file had them."""
    if not row_ids:
        return []
    out = []
    for row, imp in a.db.execute(
        select(ImportRow, Import).join(Import, Import.id == ImportRow.import_id).where(ImportRow.id.in_(row_ids))
    ):
        out.append(
            {
                "import_id": str(imp.id),
                "filename": imp.filename,
                "file_deleted": imp.state.value == "DELETED",
                "row_number": row.source_row_number,
                # Keep the file's own column order (JSON storage does not preserve it).
                "columns": [[h, row.raw.get(h, "")] for h in imp.headers if h in row.raw]
                + [[k, v] for k, v in row.raw.items() if k not in imp.headers],
                "balance_after": str(row.balance_after) if row.balance_after is not None else None,
                "imported_at": imp.completed_at or imp.created_at,
            }
        )
    return out


class ManualTxnIn(BaseModel):
    account_id: uuid.UUID
    amount: MoneyIn = Field(description="Signed: negative decreases equity (spend/charge), positive increases it.")
    transaction_date: date
    description: str = Field(min_length=1, max_length=500)
    category_id: uuid.UUID | None = None
    effect: AllocationEffect | None = None
    notes: str | None = Field(default=None, max_length=1000)
    status: TransactionStatus = TransactionStatus.POSTED


@router.post("/transactions", status_code=201)
def create_manual(
    body: ManualTxnIn, a: WriteAuth, idempotency_key: Annotated[str | None, Header(max_length=128)] = None
) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        acct = get_account(a.db, a.owner_id, body.account_id)
        cat = load_context(a.db, a.owner_id)
        t = create_transaction(
            a.db,
            a.owner_id,
            NewTransaction(
                account=acct,
                amount=body.amount,
                transaction_date=body.transaction_date,
                description=body.description,
                source=TransactionSourceKind.MANUAL,
                status=body.status,
                notes=body.notes,
            ),
            cat,
            a.actor,
        )
        if body.category_id or body.effect:
            effect = body.effect or (
                cat.categories_by_id[body.category_id].default_effect
                if body.category_id in cat.categories_by_id
                else AllocationEffect.EXPENSE
            )
            set_classification(a.db, a.owner_id, t, None, body.category_id, effect, a.actor, cat)
        audit(a.db, a.owner_id, a.actor, "transaction.create", "transaction", t.id, ["amount", "date", "description"])
        a.db.flush()
        run_matching(a.db, a.owner_id, cat, start=t.transaction_date, end_exclusive=t.transaction_date, actor="system")
        return serialize(a.db, a.owner_id, [t], cat, detail=True)[0]

    result = idempotent(a.db, a.owner_id, idempotency_key, "POST /transactions", body.model_dump(mode="json"), run)
    a.commit()
    return result


class ClassifyIn(BaseModel):
    allocation_id: uuid.UUID | None = None
    category_id: uuid.UUID | None
    effect: AllocationEffect | None = None
    merchant_name: str | None = Field(default=None, max_length=120)
    remember_merchant: bool = False


class BulkClassifyIn(BaseModel):
    transaction_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    category_id: uuid.UUID


@router.post("/transactions/classify-bulk")
def classify_bulk(body: BulkClassifyIn, a: WriteAuth) -> dict[str, int]:
    """Put several transactions in one category. Splits and confirmed transfers are left alone."""
    cat = load_context(a.db, a.owner_id)
    if body.category_id not in cat.categories_by_id:
        raise ValidationFailed("Choose a category.", code="CLASSIFICATION_REQUIRED")
    effect = cat.categories_by_id[body.category_id].default_effect
    changed = skipped = 0
    for txn_id in dict.fromkeys(body.transaction_ids):
        t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
        allocs = allocations_of(a.db, t.id)
        if len(allocs) != 1 or allocs[0].id in active_leg_allocation_ids(a.db, [allocs[0].id]):
            skipped += 1
            continue
        set_classification(a.db, a.owner_id, t, None, body.category_id, effect, a.actor, cat)
        changed += 1
    audit(a.db, a.owner_id, a.actor, "transaction.classify_bulk", "transaction", None, ["category"])
    a.commit()
    return {"changed": changed, "skipped": skipped}


@router.post("/transactions/{txn_id}/classify")
def classify(txn_id: uuid.UUID, body: ClassifyIn, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    effect = body.effect
    if effect is None:
        if body.category_id is None or body.category_id not in cat.categories_by_id:
            raise ValidationFailed("Choose a category or an effect.", code="CLASSIFICATION_REQUIRED")
        effect = cat.categories_by_id[body.category_id].default_effect
    set_classification(
        a.db,
        a.owner_id,
        t,
        body.allocation_id,
        body.category_id,
        effect,
        a.actor,
        cat,
        merchant_name=body.merchant_name,
    )
    suggestion = None
    if body.remember_merchant and body.merchant_name and body.category_id:
        m = upsert_merchant(a.db, a.owner_id, body.merchant_name, body.category_id)
        t.merchant_id = m.id
    elif body.category_id:
        # Offer a prefilled reusable rule; saving it requires a separate dry-run + confirmation.
        words = t.normalized_description.split()[:3]
        suggestion = {
            "name": f"{' '.join(words).title()} → {cat.categories_by_id[body.category_id].name}",
            "conditions": [{"field": "description", "op": "contains", "value": " ".join(words)}],
            "category_id": str(body.category_id),
            "effect": effect.value,
        }
    a.commit()
    return {"transaction": serialize(a.db, a.owner_id, [t], cat, detail=True)[0], "rule_suggestion": suggestion}


@router.post("/transactions/{txn_id}/unlock")
def unlock(txn_id: uuid.UUID, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    unlock_classification(a.db, a.owner_id, t, cat, a.actor)
    a.commit()
    return serialize(a.db, a.owner_id, [t], cat, detail=True)[0]


class SplitPartIn(BaseModel):
    amount: MoneyIn
    category_id: uuid.UUID | None = None
    effect: AllocationEffect
    note: str | None = Field(default=None, max_length=300)


class SplitIn(BaseModel):
    version: int
    parts: list[SplitPartIn] = Field(min_length=1, max_length=20)


@router.post("/transactions/{txn_id}/split")
def split(txn_id: uuid.UUID, body: SplitIn, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    if t.version != body.version:
        raise ConflictError("This transaction changed since you loaded it.", code="VERSION_CONFLICT")
    split_transaction(
        a.db, a.owner_id, t, [SplitPart(p.amount, p.effect, p.category_id, p.note) for p in body.parts], a.actor, cat
    )
    a.commit()
    return serialize(a.db, a.owner_id, [t], cat, detail=True)[0]


class AnnotateIn(BaseModel):
    version: int
    notes: str | None = Field(default=None, max_length=1000)
    tag_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


@router.patch("/transactions/{txn_id}")
def annotate(txn_id: uuid.UUID, body: AnnotateIn, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    if t.version != body.version:
        raise ConflictError("This transaction changed since you loaded it.", code="VERSION_CONFLICT")
    changed = []
    if body.notes is not None:
        t.notes = body.notes.strip() or None
        changed.append("notes")
    if body.tag_ids is not None:
        valid = set(
            a.db.scalars(
                select(TransactionTag.id).where(
                    TransactionTag.owner_id == a.owner_id, TransactionTag.id.in_(body.tag_ids)
                )
            )
        )
        if valid != set(body.tag_ids):
            raise NotFoundError("Tag not found.")
        a.db.execute(delete(TransactionTagLink).where(TransactionTagLink.transaction_id == t.id))
        for tid in valid:
            a.db.add(TransactionTagLink(owner_id=a.owner_id, transaction_id=t.id, tag_id=tid))
        changed.append("tags")
    t.version += 1
    audit(a.db, a.owner_id, a.actor, "transaction.annotate", "transaction", t.id, changed)
    a.commit()
    return serialize(a.db, a.owner_id, [t], cat, detail=True)[0]


class CorrectIn(BaseModel):
    version: int
    amount: MoneyIn | None = None
    transaction_date: date | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/transactions/{txn_id}/correct")
def correct(txn_id: uuid.UUID, body: CorrectIn, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    if t.version != body.version:
        raise ConflictError("This transaction changed since you loaded it.", code="VERSION_CONFLICT")
    correct_transaction(
        a.db,
        a.owner_id,
        t,
        amount=body.amount,
        transaction_date=body.transaction_date,
        description=body.description,
        reason=body.reason,
        actor=a.actor,
        cat=cat,
    )
    a.commit()
    return serialize(a.db, a.owner_id, [t], cat, detail=True)[0]


@router.delete("/transactions/{txn_id}", status_code=204)
def delete_txn(txn_id: uuid.UUID, a: WriteAuth) -> None:
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    soft_delete(a.db, a.owner_id, t, a.actor)
    a.commit()


@router.post("/transactions/{txn_id}/restore")
def restore_txn(txn_id: uuid.UUID, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    t = get_transaction(a.db, a.owner_id, txn_id, lock=True)
    restore(a.db, a.owner_id, t, a.actor)
    a.commit()
    return serialize(a.db, a.owner_id, [t], cat, detail=True)[0]


class MarkTransferIn(BaseModel):
    counterpart_transaction_id: uuid.UUID


@router.post("/transactions/{txn_id}/mark-transfer")
def mark_transfer(txn_id: uuid.UUID, body: MarkTransferIn, a: WriteAuth) -> dict[str, Any]:
    """Manually pair two movements as an own-account transfer (both must be unsplit)."""
    cat = load_context(a.db, a.owner_id)
    t1 = get_transaction(a.db, a.owner_id, txn_id)
    t2 = get_transaction(a.db, a.owner_id, body.counterpart_transaction_id)
    a1, a2 = allocations_of(a.db, t1.id), allocations_of(a.db, t2.id)
    pick1 = [x for x in a1 if x.effect != AllocationEffect.EXPENSE or len(a1) == 1]
    pick2 = [x for x in a2 if x.effect != AllocationEffect.EXPENSE or len(a2) == 1]
    if (len(a1) != 1 and len(pick1) != 1) or (len(a2) != 1 and len(pick2) != 1):
        raise ValidationFailed("Split transactions need the specific split selected.", code="ALLOCATION_REQUIRED")
    transfer = confirm_pair(
        a.db,
        a.owner_id,
        (a1 if len(a1) == 1 else pick1)[0].id,
        (a2 if len(a2) == 1 else pick2)[0].id,
        cat,
        actor=a.actor,
        method="MANUAL",
        reason="user_marked",
    )
    a.commit()
    return {"transfer_id": str(transfer.id), "status": transfer.status.value}
