"""Faster manual entry: transfers between your accounts, saved templates, receipt attachments."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, UploadFile
from fastapi.responses import Response
from oneledger_db.models import Transaction, TransactionAttachment, TransactionTemplate
from oneledger_domain.enums import TransactionSourceKind
from oneledger_shared.crypto import sha256_hex
from oneledger_shared.errors import NotFoundError, ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..deps import MoneyIn, ReadAuth, WriteAuth
from ..services.audit import audit
from ..services.categories import load_context
from ..services.idempotency import idempotent
from ..services.ledger import NewTransaction, allocations_of, create_transaction, get_account, get_transaction
from ..services.transfers import confirm_pair

router = APIRouter(tags=["entry"])

# --- Transfers between your own accounts ----------------------------------------------------------


class ManualTransferIn(BaseModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: MoneyIn = Field(gt=0)
    transaction_date: date
    description: str = Field(default="", max_length=500)


@router.post("/transfers/manual", status_code=201)
def manual_transfer(
    body: ManualTransferIn, a: WriteAuth, idempotency_key: Annotated[str | None, Header(max_length=128)] = None
) -> dict[str, Any]:
    """Money moved between two of your accounts (e.g. cash into the wallet, savings to a card).

    Both movements are created and linked, so it is never counted as income or spending.
    """

    def run() -> dict[str, Any]:
        src = get_account(a.db, a.owner_id, body.from_account_id)
        dst = get_account(a.db, a.owner_id, body.to_account_id)
        if src.id == dst.id:
            raise ValidationFailed("Choose two different accounts.", code="TRANSFER_SAME_ACCOUNT")
        if src.currency != dst.currency:
            raise ValidationFailed("Both accounts must use the same currency.", code="TRANSFER_CURRENCY_MISMATCH")
        cat = load_context(a.db, a.owner_id)
        desc = body.description.strip() or f"{src.name} to {dst.name}"
        out_t = create_transaction(
            a.db,
            a.owner_id,
            NewTransaction(
                account=src,
                amount=-body.amount,
                transaction_date=body.transaction_date,
                description=desc,
                source=TransactionSourceKind.MANUAL,
            ),
            cat,
            a.actor,
        )
        in_t = create_transaction(
            a.db,
            a.owner_id,
            NewTransaction(
                account=dst,
                amount=body.amount,
                transaction_date=body.transaction_date,
                description=desc,
                source=TransactionSourceKind.MANUAL,
            ),
            cat,
            a.actor,
        )
        tr = confirm_pair(
            a.db,
            a.owner_id,
            allocations_of(a.db, out_t.id)[0].id,
            allocations_of(a.db, in_t.id)[0].id,
            cat,
            actor=a.actor,
            method="MANUAL",
            reason="manual_transfer",
        )
        return {"transfer_id": str(tr.id), "from_transaction_id": str(out_t.id), "to_transaction_id": str(in_t.id)}

    result = idempotent(a.db, a.owner_id, idempotency_key, "POST /transfers/manual", body.model_dump(mode="json"), run)
    a.commit()
    return result


# --- Templates --------------------------------------------------------------------------------------


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["expense", "income", "transfer"]
    account_id: uuid.UUID | None = None
    to_account_id: uuid.UUID | None = None
    amount: MoneyIn | None = Field(default=None, gt=0)
    category_id: uuid.UUID | None = None
    description: str = Field(default="", max_length=500)


def _template_out(t: TransactionTemplate) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "kind": t.kind,
        "account_id": str(t.account_id) if t.account_id else None,
        "to_account_id": str(t.to_account_id) if t.to_account_id else None,
        "amount": str(t.amount) if t.amount is not None else None,
        "category_id": str(t.category_id) if t.category_id else None,
        "description": t.description,
        "use_count": t.use_count,
    }


def _check_template(a: WriteAuth, body: TemplateIn) -> None:
    for acct in (body.account_id, body.to_account_id):
        if acct:
            get_account(a.db, a.owner_id, acct)
    if body.kind == "transfer" and (
        not body.account_id or not body.to_account_id or body.account_id == body.to_account_id
    ):
        raise ValidationFailed("A transfer template needs two different accounts.", code="TRANSFER_ACCOUNTS")
    if body.category_id and body.category_id not in load_context(a.db, a.owner_id).categories_by_id:
        raise NotFoundError("Category not found.")


@router.get("/templates")
def list_templates(a: ReadAuth) -> list[dict[str, Any]]:
    rows = a.db.scalars(
        select(TransactionTemplate)
        .where(TransactionTemplate.owner_id == a.owner_id)
        .order_by(TransactionTemplate.use_count.desc(), TransactionTemplate.name)
    )
    return [_template_out(t) for t in rows]


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, a: WriteAuth) -> dict[str, Any]:
    _check_template(a, body)
    t = TransactionTemplate(
        owner_id=a.owner_id,
        name=body.name.strip(),
        kind=body.kind,
        account_id=body.account_id,
        to_account_id=body.to_account_id if body.kind == "transfer" else None,
        amount=body.amount,
        category_id=body.category_id if body.kind != "transfer" else None,
        description=body.description.strip() or body.name.strip(),
    )
    a.db.add(t)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "template.create", "template", t.id, ["name"])
    a.commit()
    return _template_out(t)


def _template(a: ReadAuth | WriteAuth, template_id: uuid.UUID) -> TransactionTemplate:
    t = a.db.get(TransactionTemplate, template_id)
    if t is None or t.owner_id != a.owner_id:
        raise NotFoundError()
    return t


@router.post("/templates/{template_id}/used")
def template_used(template_id: uuid.UUID, a: WriteAuth) -> dict[str, Any]:
    t = _template(a, template_id)
    t.use_count += 1
    t.last_used_at = datetime.now(UTC)
    a.commit()
    return _template_out(t)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: uuid.UUID, a: WriteAuth) -> None:
    t = _template(a, template_id)
    a.db.execute(delete(TransactionTemplate).where(TransactionTemplate.id == t.id))
    audit(a.db, a.owner_id, a.actor, "template.delete", "template", t.id, ["deleted"])
    a.commit()


# --- Receipt attachments --------------------------------------------------------------------------

ATTACHMENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"}
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS_PER_TXN = 10
_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"%PDF-": "application/pdf",
}


def _sniff(data: bytes, declared: str) -> str | None:
    """The declared type must agree with the file's first bytes (HEIC/WebP are checked by marker)."""
    for magic, kind in _MAGIC.items():
        if data.startswith(magic):
            return kind if declared in (kind, "application/octet-stream") else None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heif"):
        return "image/heic"
    return None


def _attachment_out(x: TransactionAttachment) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "transaction_id": str(x.transaction_id),
        "filename": x.filename,
        "content_type": x.content_type,
        "size_bytes": x.size_bytes,
        "created_at": x.created_at,
    }


@router.get("/transactions/{txn_id}/attachments")
def list_attachments(txn_id: uuid.UUID, a: ReadAuth) -> list[dict[str, Any]]:
    get_transaction(a.db, a.owner_id, txn_id)
    rows = a.db.scalars(
        select(TransactionAttachment)
        .where(TransactionAttachment.owner_id == a.owner_id, TransactionAttachment.transaction_id == txn_id)
        .order_by(TransactionAttachment.created_at)
    )
    return [_attachment_out(x) for x in rows]


@router.post("/transactions/{txn_id}/attachments", status_code=201)
async def add_attachment(txn_id: uuid.UUID, file: UploadFile, a: WriteAuth) -> dict[str, Any]:
    txn: Transaction = get_transaction(a.db, a.owner_id, txn_id)
    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not data:
        raise ValidationFailed("The file is empty.", code="EMPTY_FILE")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValidationFailed("Receipts can be up to 5 MB.", code="FILE_TOO_LARGE")
    kind = _sniff(data, (file.content_type or "application/octet-stream").lower())
    if kind is None or kind not in ATTACHMENT_TYPES:
        raise ValidationFailed("Attach a photo (JPEG, PNG, WebP, HEIC) or a PDF.", code="UNSUPPORTED_FILE")
    count = len(
        list(
            a.db.scalars(
                select(TransactionAttachment.id).where(
                    TransactionAttachment.owner_id == a.owner_id, TransactionAttachment.transaction_id == txn.id
                )
            )
        )
    )
    if count >= MAX_ATTACHMENTS_PER_TXN:
        raise ValidationFailed("A transaction can have up to 10 attachments.", code="TOO_MANY_ATTACHMENTS")
    digest = sha256_hex(data)
    name = (
        "".join(ch for ch in (file.filename or "receipt") if ch.isprintable() and ch not in '/\\"')[:160] or "receipt"
    )
    x = TransactionAttachment(
        owner_id=a.owner_id,
        transaction_id=txn.id,
        filename=name,
        content_type=kind,
        size_bytes=len(data),
        sha256=digest,
        content_enc=a.ctx.box.encrypt(data, associated_data=digest.encode()),
        key_version=a.ctx.box.version,
    )
    a.db.add(x)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "attachment.add", "transaction", txn.id, ["attachments"])
    a.commit()
    return _attachment_out(x)


def _attachment(a: ReadAuth | WriteAuth, attachment_id: uuid.UUID) -> TransactionAttachment:
    x = a.db.get(TransactionAttachment, attachment_id)
    if x is None or x.owner_id != a.owner_id:
        raise NotFoundError()
    return x


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: uuid.UUID, a: ReadAuth) -> Response:
    x = _attachment(a, attachment_id)
    data = a.ctx.box.decrypt(x.content_enc, associated_data=x.sha256.encode())
    return Response(
        content=data,
        media_type=x.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{x.filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: uuid.UUID, a: WriteAuth) -> None:
    x = _attachment(a, attachment_id)
    a.db.execute(delete(TransactionAttachment).where(TransactionAttachment.id == x.id))
    audit(a.db, a.owner_id, a.actor, "attachment.delete", "transaction", x.transaction_id, ["attachments"])
    a.commit()
