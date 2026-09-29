"""Scoped read-only API tokens (for the MCP server), audit history and secure export."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from oneledger_db.models import (
    Account,
    ApiToken,
    AuditLog,
    Transaction,
    TransactionAllocation,
    TransactionCategory,
)
from oneledger_shared.errors import NotFoundError
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..deps import AdminAuth, ReadAuth
from ..security.auth import SCOPE_READ, issue_api_token
from ..services.audit import audit
from ..services.imports import _formula_safe
from ..services.reports import active_txn_filter

router = APIRouter(tags=["tokens"])


class TokenIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: [SCOPE_READ], min_length=1, max_length=2)
    expires_in_days: int = Field(default=90, ge=1, le=365)


@router.get("/tokens")
def list_tokens(a: AdminAuth) -> list[dict[str, Any]]:
    rows = a.db.scalars(select(ApiToken).where(ApiToken.owner_id == a.owner_id).order_by(ApiToken.created_at.desc()))
    return [
        {
            "id": str(t.id),
            "label": t.label,
            "prefix": t.token_prefix,
            "scopes": t.scopes,
            "expires_at": t.expires_at,
            "last_used_at": t.last_used_at,
            "revoked": t.revoked_at is not None,
            "created_at": t.created_at,
        }
        for t in rows
    ]


@router.post("/tokens", status_code=201)
def create_token(body: TokenIn, a: AdminAuth) -> dict[str, Any]:
    """The token is shown exactly once. Issuing it is a data-disclosure decision for the connected AI client."""
    a.principal.require_strong()
    token, row = issue_api_token(a.ctx, a.db, a.owner_id, body.label, body.scopes, body.expires_in_days)
    audit(a.db, a.owner_id, a.actor, "token.create", "api_token", row.id, ["scopes"])
    a.commit()
    return {
        "id": str(row.id),
        "token": token,
        "scopes": row.scopes,
        "expires_at": row.expires_at,
        "warning": "Copy this token now; it cannot be shown again.",
    }


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: uuid.UUID, a: AdminAuth) -> None:
    res = a.db.execute(
        update(ApiToken)
        .where(ApiToken.id == token_id, ApiToken.owner_id == a.owner_id, ApiToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    if res.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError()
    audit(a.db, a.owner_id, a.actor, "token.revoke", "api_token", token_id, ["revoked_at"])
    a.commit()


@router.get("/audit")
def audit_history(a: AdminAuth, limit: int = 100) -> list[dict[str, Any]]:
    rows = a.db.scalars(
        select(AuditLog)
        .where(AuditLog.owner_id == a.owner_id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 500))
    )
    return [
        {
            "id": str(r.id),
            "actor": r.actor,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id) if r.entity_id else None,
            "changed_fields": r.changed_fields,
            "at": r.created_at,
            "request_id": r.correlation_id,
        }
        for r in rows
    ]


@router.get("/export/transactions.csv", response_class=PlainTextResponse)
def export_csv(a: AdminAuth) -> PlainTextResponse:
    """Full export; requires MFA when enabled. Cells are escaped against spreadsheet formula injection."""
    a.principal.require_strong()
    cats = {
        c.id: c.name
        for c in a.db.scalars(select(TransactionCategory).where(TransactionCategory.owner_id == a.owner_id))
    }
    accts = {x.id: x.name for x in a.db.scalars(select(Account).where(Account.owner_id == a.owner_id))}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["date", "account", "amount", "currency", "description", "merchant", "effect", "category", "split_amount"]
    )
    rows = a.db.execute(
        select(Transaction, TransactionAllocation)
        .join(TransactionAllocation, TransactionAllocation.transaction_id == Transaction.id)
        .where(Transaction.owner_id == a.owner_id, active_txn_filter())
        .order_by(Transaction.transaction_date, Transaction.id)
    )
    for t, al in rows:
        w.writerow(
            [
                t.transaction_date.isoformat(),
                _formula_safe(accts.get(t.account_id, "")),
                str(t.amount),
                t.currency,
                _formula_safe(t.raw_description),
                _formula_safe(t.merchant_name or ""),
                al.effect.value,
                _formula_safe(cats.get(al.category_id, "") if al.category_id else ""),
                str(al.amount),
            ]
        )
    audit(a.db, a.owner_id, a.actor, "export.transactions", "export", None, [])
    a.commit()
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="oneledger-transactions.csv"'},
    )


@router.get("/token-info")
def token_info(a: ReadAuth) -> dict[str, Any]:
    """Lets the MCP server verify its credential and scopes."""
    return {"kind": a.principal.kind, "scopes": sorted(a.principal.scopes)}


def _plain(row: Any, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    """Column values of an ORM row as JSON-friendly values (owner id and encrypted blobs left out)."""
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        if col.name in ("owner_id", *skip) or col.name.endswith("_enc"):
            continue
        v = getattr(row, col.key)
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, list):
            v = [str(x) if isinstance(x, uuid.UUID) else x for x in v]
        elif hasattr(v, "value") and not isinstance(v, (int, float, str, bool)):
            v = v.value
        elif v is not None and not isinstance(v, (int, float, str, bool, dict)):
            v = str(v)
        out[col.name] = v
    return out


@router.get("/export/ledger.json")
def export_json(a: AdminAuth) -> JSONResponse:
    """Everything in your ledger as one JSON file: a backup you can keep or read with other tools.

    Uploaded statement files and receipt images are not included (they stay encrypted on the server).
    """
    from oneledger_db.models import (
        BalanceSnapshot,
        Budget,
        CreditCard,
        CreditCardStatement,
        FinancialGoal,
        GoalContribution,
        Investment,
        InvestmentTransaction,
        InvestmentValuation,
        Loan,
        LoanPayment,
        LoanRateChange,
        Person,
        RecurringTransaction,
        TransactionCategoryRule,
        TransactionMerchant,
        TransactionTag,
        TransactionTagLink,
        TransactionTemplate,
        Transfer,
        TransferLeg,
    )

    a.principal.require_strong()
    tables: dict[str, Any] = {
        "accounts": Account,
        "balance_snapshots": BalanceSnapshot,
        "categories": TransactionCategory,
        "rules": TransactionCategoryRule,
        "merchants": TransactionMerchant,
        "tags": TransactionTag,
        "tag_links": TransactionTagLink,
        "transfers": Transfer,
        "transfer_legs": TransferLeg,
        "budgets": Budget,
        "goals": FinancialGoal,
        "goal_contributions": GoalContribution,
        "loans": Loan,
        "loan_rate_changes": LoanRateChange,
        "loan_payments": LoanPayment,
        "credit_cards": CreditCard,
        "card_statements": CreditCardStatement,
        "investments": Investment,
        "investment_transactions": InvestmentTransaction,
        "investment_valuations": InvestmentValuation,
        "recurring": RecurringTransaction,
        "people": Person,
        "templates": TransactionTemplate,
    }
    data: dict[str, Any] = {
        "format": "oneledger-export-v1",
        "exported_at": datetime.now(UTC).isoformat(),
    }
    for name, model in tables.items():
        data[name] = [_plain(r) for r in a.db.scalars(select(model).where(model.owner_id == a.owner_id))]
    txns = a.db.scalars(
        select(Transaction).where(Transaction.owner_id == a.owner_id, Transaction.deleted_at.is_(None))
    ).all()
    allocs: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for al in a.db.scalars(select(TransactionAllocation).where(TransactionAllocation.owner_id == a.owner_id)):
        allocs.setdefault(al.transaction_id, []).append(_plain(al))
    data["transactions"] = [{**_plain(t), "allocations": allocs.get(t.id, [])} for t in txns]
    audit(a.db, a.owner_id, a.actor, "export.ledger", "export", None, [])
    a.commit()
    return JSONResponse(
        data,
        headers={"Content-Disposition": 'attachment; filename="oneledger-export.json"'},
    )
