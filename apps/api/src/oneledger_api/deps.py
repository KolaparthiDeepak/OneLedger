"""Request dependencies: DB session, authentication and scope checks."""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from oneledger_db.models import User
from oneledger_db.session import OWNER_KEY
from oneledger_shared.errors import AuthenticationError, NotFoundError
from pydantic import BeforeValidator
from sqlalchemy.orm import Session

from .context import AppContext, get_context
from .security.auth import SCOPE_ADMIN, SCOPE_READ, SCOPE_READ_DETAIL, SCOPE_WRITE, Principal, authenticate


def ctx_dep() -> AppContext:
    return get_context()


def db_dep(ctx: Annotated[AppContext, Depends(ctx_dep)]) -> Iterator[Session]:
    db = ctx.sessions()
    db.info[OWNER_KEY] = None
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        db.rollback()
        db.close()


@dataclass
class Authed:
    principal: Principal
    db: Session
    ctx: AppContext

    @property
    def owner_id(self) -> uuid.UUID:
        return self.principal.owner_id

    @property
    def actor(self) -> str:
        return self.principal.actor

    def user(self) -> User:
        u = self.db.get(User, self.owner_id)
        if u is None:
            raise NotFoundError()
        return u

    def commit(self) -> None:
        self.db.commit()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError()
    token = authorization[7:].strip()
    if not token or len(token) > 200:
        raise AuthenticationError()
    return token


def _authed(scope: str | None, *, allow_mfa_pending: bool = False) -> Callable[..., Authed]:
    def dep(
        ctx: Annotated[AppContext, Depends(ctx_dep)],
        db: Annotated[Session, Depends(db_dep)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> Authed:
        principal = authenticate(ctx, db, _bearer(authorization))
        db.commit()  # persist last-seen bookkeeping; owner context is re-applied per transaction
        if not (allow_mfa_pending and principal.kind == "session") and scope:
            principal.require(scope)
        return Authed(principal, db, ctx)

    return dep


ReadAuth = Annotated[Authed, Depends(_authed(SCOPE_READ))]
DetailAuth = Annotated[Authed, Depends(_authed(SCOPE_READ_DETAIL))]
WriteAuth = Annotated[Authed, Depends(_authed(SCOPE_WRITE))]
AdminAuth = Annotated[Authed, Depends(_authed(SCOPE_ADMIN))]
EnrolAuth = Annotated[Authed, Depends(_authed(None, allow_mfa_pending=True))]
DB = Annotated[Session, Depends(db_dep)]
Ctx = Annotated[AppContext, Depends(ctx_dep)]


def _no_float(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("send money as a decimal string, not a JSON number")
    return v


MoneyIn = Annotated[Decimal, BeforeValidator(_no_float)]


def client_key(request: Request, ctx: AppContext) -> str:
    """Rate-limit key. The forwarded client IP is only trusted when the BFF proves the shared secret."""
    secret = ctx.settings.bff_shared_secret.get_secret_value()
    fwd = request.headers.get("x-oneledger-client-ip")
    presented = request.headers.get("x-oneledger-bff", "")
    if fwd and secret and hmac.compare_digest(presented, secret):
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"
