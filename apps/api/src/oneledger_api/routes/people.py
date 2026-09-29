"""People you share expenses with: who owes whom, splitting a payment, settling up."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from oneledger_db.models import Person, Transaction
from oneledger_domain.periods import today_in
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import MoneyIn, ReadAuth, WriteAuth
from ..security.auth import SCOPE_READ_DETAIL
from ..services import people as svc
from ..services.categories import load_context
from ..services.reports import active_txn_filter
from ..services.txn_query import serialize

router = APIRouter(tags=["people"])


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=320)


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


@router.get("/people")
def list_people(a: ReadAuth, include_archived: bool = False) -> list[dict[str, Any]]:
    today = today_in(a.user().timezone)
    q = select(Person).where(Person.owner_id == a.owner_id)
    if not include_archived:
        q = q.where(Person.archived_at.is_(None))
    return [svc.person_out(a.db, a.owner_id, p, today) for p in a.db.scalars(q.order_by(Person.name))]


@router.post("/people", status_code=201)
def create_person(body: PersonIn, a: WriteAuth) -> dict[str, Any]:
    user = a.user()
    p = svc.create_person(
        a.db, a.owner_id, name=body.name, email=body.email, currency=user.base_currency, actor=a.actor
    )
    a.commit()
    return svc.person_out(a.db, a.owner_id, p, today_in(user.timezone))


@router.get("/people/{person_id}")
def person_detail(person_id: uuid.UUID, a: ReadAuth) -> dict[str, Any]:
    p = svc.get_person(a.db, a.owner_id, person_id)
    out = svc.person_out(a.db, a.owner_id, p, today_in(a.user().timezone))
    txns = list(
        a.db.scalars(
            select(Transaction)
            .where(Transaction.owner_id == a.owner_id, Transaction.account_id == p.account_id, active_txn_filter())
            .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
            .limit(200)
        )
    )
    out["activity"] = serialize(
        a.db, a.owner_id, txns, load_context(a.db, a.owner_id), detail=SCOPE_READ_DETAIL in a.principal.scopes
    )
    return out


@router.patch("/people/{person_id}")
def update_person(person_id: uuid.UUID, body: PersonPatch, a: WriteAuth) -> dict[str, Any]:
    p = svc.get_person(a.db, a.owner_id, person_id)
    if body.name:
        p.name = body.name.strip()[:120]
    if body.archived is not None:
        p.archived_at = datetime.now(UTC) if body.archived else None
    a.commit()
    return svc.person_out(a.db, a.owner_id, p, today_in(a.user().timezone))


class ShareIn(BaseModel):
    person_id: uuid.UUID
    amount: MoneyIn


class SharesIn(BaseModel):
    shares: list[ShareIn] = Field(min_length=1, max_length=20)


@router.post("/transactions/{txn_id}/share")
def share(txn_id: uuid.UUID, body: SharesIn, a: WriteAuth) -> dict[str, Any]:
    cat = load_context(a.db, a.owner_id)
    out = svc.share_expense(a.db, a.owner_id, txn_id, [(s.person_id, s.amount) for s in body.shares], cat, a.actor)
    a.commit()
    return {"shares": out}


@router.delete("/transactions/{txn_id}/share")
def unshare(txn_id: uuid.UUID, a: WriteAuth) -> dict[str, str]:
    svc.unshare_expense(a.db, a.owner_id, txn_id, load_context(a.db, a.owner_id), a.actor)
    a.commit()
    return {"status": "removed"}


class TheyPaidIn(BaseModel):
    amount: MoneyIn = Field(description="Your share, positive.")
    transaction_date: date
    description: str = Field(min_length=1, max_length=500)
    category_id: uuid.UUID | None = None


@router.post("/people/{person_id}/they-paid", status_code=201)
def they_paid(person_id: uuid.UUID, body: TheyPaidIn, a: WriteAuth) -> dict[str, Any]:
    p = svc.get_person(a.db, a.owner_id, person_id)
    svc.they_paid(
        a.db,
        a.owner_id,
        p,
        amount=body.amount,
        on=body.transaction_date,
        description=body.description,
        category_id=body.category_id,
        cat=load_context(a.db, a.owner_id),
        actor=a.actor,
    )
    a.commit()
    return svc.person_out(a.db, a.owner_id, p, today_in(a.user().timezone))


class SettleIn(BaseModel):
    account_id: uuid.UUID
    amount: MoneyIn = Field(description="Positive: they paid you. Negative: you paid them.")
    transaction_date: date
    transaction_id: uuid.UUID | None = None


@router.post("/people/{person_id}/settle", status_code=201)
def settle(person_id: uuid.UUID, body: SettleIn, a: WriteAuth) -> dict[str, Any]:
    p = svc.get_person(a.db, a.owner_id, person_id)
    svc.settle(
        a.db,
        a.owner_id,
        p,
        account_id=body.account_id,
        amount=Decimal(body.amount),
        on=body.transaction_date,
        transaction_id=body.transaction_id,
        cat=load_context(a.db, a.owner_id),
        actor=a.actor,
    )
    a.commit()
    return svc.person_out(a.db, a.owner_id, p, today_in(a.user().timezone))
