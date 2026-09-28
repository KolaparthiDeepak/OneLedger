"""Accounts, opening balances and balance snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter
from oneledger_db.models import Account, BalanceSnapshot, CashAccount, FinancialInstitution
from oneledger_domain.enums import AccountKind, AccountStatus, BalanceKind, BalanceSource
from oneledger_domain.money import validate_precision
from oneledger_domain.periods import today_in
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from oneledger_shared.logging import mask_account_number
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import MoneyIn, ReadAuth, WriteAuth
from ..services.audit import audit, bump_ledger_revision
from ..services.ledger import get_account
from ..services.reconcile import reconcile_account
from ..services.reports import account_balances

router = APIRouter(tags=["accounts"])


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind
    currency: str = Field(default="INR", min_length=3, max_length=3)
    institution_name: str | None = Field(default=None, max_length=120)
    account_number: str | None = Field(default=None, max_length=40, description="Masked before storage.")
    include_in_net_worth: bool = True
    opening_date: date | None = None
    opening_balance: MoneyIn | None = Field(
        default=None,
        description="Natural balance at end of opening_date: amount held for assets, amount owed for liabilities.",
    )
    notes: str | None = Field(default=None, max_length=500)


class AccountPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    include_in_net_worth: bool | None = None
    status: AccountStatus | None = None
    notes: str | None = Field(default=None, max_length=500)


def _institution(db: Session, name: str | None) -> uuid.UUID | None:
    if not name:
        return None
    code = "".join(ch for ch in name.upper() if ch.isalnum())[:40] or "OTHER"
    inst = db.scalars(select(FinancialInstitution).where(FinancialInstitution.code == code)).first()
    if inst is None:
        inst = FinancialInstitution(code=code, name=name.strip()[:120])
        db.add(inst)
        db.flush()
    return inst.id


def account_out(a: Account, institution: str | None = None) -> dict[str, object]:
    return {
        "id": str(a.id),
        "name": a.name,
        "kind": a.kind.value,
        "nature": a.kind.nature.value,
        "currency": a.currency,
        "masked_identifier": a.masked_identifier,
        "status": a.status.value,
        "institution": institution,
        "include_in_net_worth": a.include_in_net_worth,
        "opening_date": a.opening_date,
        "is_synthetic": a.is_synthetic,
        "notes": a.notes,
        "version": a.version,
        "created_at": a.created_at,
    }


@router.get("/accounts")
def list_accounts(a: ReadAuth, include_archived: bool = False) -> list[dict[str, object]]:
    stmt = (
        select(Account, FinancialInstitution.name)
        .outerjoin(FinancialInstitution, FinancialInstitution.id == Account.institution_id)
        .where(Account.owner_id == a.owner_id, Account.deleted_at.is_(None))
    )
    if not include_archived:
        stmt = stmt.where(Account.status == AccountStatus.ACTIVE)
    rows = a.db.execute(stmt.order_by(Account.kind, Account.name)).all()
    tz = a.user().timezone
    balances = {b.account.id: b for b in account_balances(a.db, a.owner_id, today_in(tz), [r[0] for r in rows])}
    out = []
    for acct, inst in rows:
        item = account_out(acct, inst)
        b = balances.get(acct.id)
        item["balance"] = str(b.balance) if b and b.balance is not None else None
        item["balance_as_of"] = b.derived_through if b else None
        item["balance_stale"] = b.stale if b else False
        out.append(item)
    return out


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, a: WriteAuth) -> dict[str, object]:
    currency = body.currency.upper()
    if body.opening_balance is not None:
        if body.opening_date is None:
            raise ValidationFailed("An opening balance needs an as-of date.", code="OPENING_DATE_REQUIRED")
        validate_precision(body.opening_balance, currency)
        if body.kind.nature.value == "LIABILITY" and body.opening_balance < 0:
            raise ValidationFailed("Enter the amount owed as a positive number.", code="NEGATIVE_LIABILITY")
    acct = Account(
        owner_id=a.owner_id,
        institution_id=_institution(a.db, body.institution_name),
        name=body.name.strip(),
        kind=body.kind,
        currency=currency,
        masked_identifier=mask_account_number(body.account_number),
        include_in_net_worth=body.include_in_net_worth,
        opening_date=body.opening_date,
        notes=body.notes,
    )
    a.db.add(acct)
    a.db.flush()
    if body.kind == AccountKind.CASH:
        a.db.add(CashAccount(owner_id=a.owner_id, account_id=acct.id))
    if body.opening_balance is not None and body.opening_date is not None:
        a.db.add(
            BalanceSnapshot(
                owner_id=a.owner_id,
                account_id=acct.id,
                amount=body.opening_balance,
                currency=currency,
                balance_kind=BalanceKind.OPENING,
                as_of=body.opening_date,
                source=BalanceSource.MANUAL,
                is_observed=True,
                source_ref="opening",
            )
        )
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "account.create", "account", acct.id, ["name", "kind", "currency"])
    a.commit()
    return account_out(acct, body.institution_name)


@router.get("/accounts/{account_id}")
def get_one(account_id: uuid.UUID, a: ReadAuth) -> dict[str, object]:
    acct = get_account(a.db, a.owner_id, account_id)
    inst = a.db.get(FinancialInstitution, acct.institution_id) if acct.institution_id else None
    item = account_out(acct, inst.name if inst else None)
    tz = a.user().timezone
    b = account_balances(a.db, a.owner_id, today_in(tz), [acct])[0]
    item["balance"] = str(b.balance) if b.balance is not None else None
    item["balance_observed_as_of"] = b.observed_as_of
    item["balance_as_of"] = b.derived_through
    item["transactions_after_snapshot"] = b.transactions_after_snapshot
    item["balance_stale"] = b.stale
    return item


@router.patch("/accounts/{account_id}")
def update_account(account_id: uuid.UUID, body: AccountPatch, a: WriteAuth) -> dict[str, object]:
    acct = get_account(a.db, a.owner_id, account_id)
    if acct.version != body.version:
        raise ConflictError("This account changed since you loaded it.", code="VERSION_CONFLICT")
    changed = []
    for field in ("name", "include_in_net_worth", "status", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(acct, field, value)
            changed.append(field)
    acct.version += 1
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "account.update", "account", acct.id, changed)
    a.commit()
    return account_out(acct)


class BalanceIn(BaseModel):
    amount: MoneyIn = Field(description="Natural balance: held for assets, owed for liabilities.")
    as_of: date
    balance_kind: BalanceKind = BalanceKind.CURRENT


@router.get("/accounts/{account_id}/balances")
def balance_history(account_id: uuid.UUID, a: ReadAuth) -> list[dict[str, object]]:
    get_account(a.db, a.owner_id, account_id)
    rows = a.db.scalars(
        select(BalanceSnapshot)
        .where(
            BalanceSnapshot.account_id == account_id,
            BalanceSnapshot.owner_id == a.owner_id,
            BalanceSnapshot.deleted_at.is_(None),
        )
        .order_by(BalanceSnapshot.as_of.desc())
        .limit(200)
    ).all()
    return [
        {
            "id": str(s.id),
            "amount": str(s.amount),
            "currency": s.currency,
            "kind": s.balance_kind.value,
            "as_of": s.as_of,
            "source": s.source.value,
            "observed": s.is_observed,
            "reconciliation": s.reconciliation_state,
        }
        for s in rows
    ]


@router.post("/accounts/{account_id}/balances", status_code=201)
def add_balance(account_id: uuid.UUID, body: BalanceIn, a: WriteAuth) -> dict[str, object]:
    acct = get_account(a.db, a.owner_id, account_id)
    validate_precision(body.amount, acct.currency)
    if acct.kind.nature.value == "LIABILITY" and body.amount < 0:
        raise ValidationFailed("Enter the amount owed as a positive number.", code="NEGATIVE_LIABILITY")
    if body.as_of > today_in(a.user().timezone):
        raise ValidationFailed("Balances cannot be dated in the future.", code="FUTURE_BALANCE")
    snap = BalanceSnapshot(
        owner_id=a.owner_id,
        account_id=acct.id,
        amount=body.amount,
        currency=acct.currency,
        balance_kind=body.balance_kind,
        as_of=body.as_of,
        source=BalanceSource.MANUAL,
        is_observed=True,
    )
    a.db.add(snap)
    a.db.flush()
    result = reconcile_account(a.db, a.owner_id, acct, snap)
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "balance.create", "account", acct.id, ["balance"])
    a.commit()
    return {"id": str(snap.id), "reconciliation": result}


@router.delete("/accounts/{account_id}/balances/{snapshot_id}", status_code=204)
def delete_balance(account_id: uuid.UUID, snapshot_id: uuid.UUID, a: WriteAuth) -> None:
    snap = a.db.get(BalanceSnapshot, snapshot_id)
    if snap is None or snap.owner_id != a.owner_id or snap.account_id != account_id:
        raise NotFoundError()
    snap.deleted_at = datetime.now(UTC)
    bump_ledger_revision(a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "balance.delete", "account", account_id, ["balance"])
    a.commit()
