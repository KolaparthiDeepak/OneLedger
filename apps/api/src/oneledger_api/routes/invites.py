"""Invite links. A signed-in person can invite someone to create their own, separate ledger.

There is still no public sign-up: accepting requires a valid, unused, unexpired invite token.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from oneledger_db.models import Invite, User
from oneledger_shared.errors import ConflictError, NotFoundError, RateLimited, ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from ..deps import DB, AdminAuth, Ctx, ReadAuth, client_key
from ..security.auth import login
from ..security.ratelimit import hit
from ..services.audit import audit
from ..services.users import MIN_PASSWORD, email_taken, provision_user

router = APIRouter(tags=["invites"])

INVITE_DAYS = 7
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
INVALID = "This invite link is not valid. It may have been used, revoked or expired; ask for a new one."


class InviteIn(BaseModel):
    email: str | None = Field(default=None, max_length=320, pattern=EMAIL)
    note: str | None = Field(default=None, max_length=120)


def _status(i: Invite, now: datetime) -> str:
    if i.used_at:
        return "used"
    if i.revoked_at:
        return "revoked"
    return "expired" if i.expires_at <= now else "pending"


def invite_out(i: Invite, now: datetime, used_by_email: str | None = None) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "email": i.email,
        "note": i.note,
        "created_at": i.created_at,
        "expires_at": i.expires_at,
        "status": _status(i, now),
        "used_at": i.used_at,
        "used_by_email": used_by_email,
    }


@router.post("/invites", status_code=201)
def create_invite(body: InviteIn, a: AdminAuth) -> dict[str, Any]:
    if not hit(a.db, f"invite:create:{a.owner_id}", limit=20, window_seconds=86400):
        raise RateLimited("You have created a lot of invites today. Try again tomorrow.")
    email = body.email.strip().lower() if body.email else None
    if email and email_taken(a.db, email):
        raise ConflictError("That email already has a ledger here.", code="EMAIL_TAKEN")
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    inv = Invite(
        owner_id=a.owner_id,
        email=email,
        note=(body.note or "").strip() or None,
        token_hash=a.ctx.token_hash(token),
        created_at=now,
        expires_at=now + timedelta(days=INVITE_DAYS),
    )
    a.db.add(inv)
    a.db.flush()
    audit(a.db, a.owner_id, a.actor, "invite.create", "invite", inv.id, ["email"])
    a.commit()
    # The link is shown once; only its hash is stored.
    return {**invite_out(inv, now), "url": f"{a.ctx.settings.app_base_url.rstrip('/')}/invite/{token}"}


@router.get("/invites")
def list_invites(a: ReadAuth) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    rows = a.db.scalars(select(Invite).where(Invite.owner_id == a.owner_id).order_by(Invite.created_at.desc())).all()
    # Only the email the invitee signed up with is shown, never anything from their ledger.
    emails: dict[uuid.UUID, str] = {}
    for i in rows:
        if i.used_by:
            row = a.db.execute(text("SELECT email FROM oneledger.invite_joined_email(:i)"), {"i": i.id}).first()
            if row:
                emails[i.used_by] = row[0]
    return [invite_out(i, now, emails.get(i.used_by) if i.used_by else None) for i in rows]


@router.post("/invites/{invite_id}/revoke")
def revoke_invite(invite_id: uuid.UUID, a: AdminAuth) -> dict[str, Any]:
    inv = a.db.get(Invite, invite_id)
    if inv is None or inv.owner_id != a.owner_id:
        raise NotFoundError()
    now = datetime.now(UTC)
    if inv.used_at is None and inv.revoked_at is None:
        inv.revoked_at = now
        audit(a.db, a.owner_id, a.actor, "invite.revoke", "invite", inv.id, ["revoked_at"])
    a.commit()
    return invite_out(inv, now)


# --- Public: the invitee is not signed in ---------------------------------------------------


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=100)


def _limit(db: DB, request: Request, ctx: Ctx) -> None:
    if not hit(db, f"invite:ip:{client_key(request, ctx)}", limit=30, window_seconds=900):
        db.commit()
        raise RateLimited("Too many attempts. Try again later.")
    db.commit()


def _lookup(db: DB, ctx: Ctx, token: str) -> dict[str, Any]:
    row = db.execute(text("SELECT * FROM oneledger.invite_lookup(:h)"), {"h": ctx.token_hash(token)}).mappings().first()
    if row is None:
        raise NotFoundError(INVALID, code="INVITE_INVALID")
    return dict(row)


@router.post("/invites/check")
def check_invite(body: TokenIn, request: Request, ctx: Ctx, db: DB) -> dict[str, Any]:
    _limit(db, request, ctx)
    row = _lookup(db, ctx, body.token)
    return {"email": row["email"], "invited_by": row["inviter_name"], "expires_at": row["expires_at"]}


class AcceptIn(TokenIn):
    email: str = Field(min_length=3, max_length=320, pattern=EMAIL)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=MIN_PASSWORD, max_length=256)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)


@router.post("/invites/accept", status_code=201)
def accept_invite(body: AcceptIn, request: Request, ctx: Ctx, db: DB) -> dict[str, Any]:
    _limit(db, request, ctx)
    row = _lookup(db, ctx, body.token)
    email = body.email.strip().lower()
    if row["email"] and row["email"] != email:
        raise ValidationFailed("This invite is for a different email address.", code="INVITE_EMAIL_MISMATCH")
    if email_taken(db, email):
        raise ConflictError("That email already has a ledger here. Sign in instead.", code="EMAIL_TAKEN")
    try:
        timezone = str(ZoneInfo(body.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        timezone = ctx.settings.default_timezone
    user: User = provision_user(
        ctx,
        db,
        email=email,
        name=body.name,
        password=body.password,
        timezone=timezone,
        currency=ctx.settings.default_currency,
    )
    # Consumed in the same transaction as the account is created: one invite, one ledger.
    used = db.execute(
        text("SELECT oneledger.invite_consume(:h, :u)"), {"h": ctx.token_hash(body.token), "u": user.id}
    ).scalar()
    if used is None:
        db.rollback()
        raise NotFoundError(INVALID, code="INVITE_INVALID")
    audit(db, user.id, "user", "invite.accept", "invite", used, ["account"])
    db.commit()
    token, session, _ = login(
        ctx, db, email, body.password, client_key(request, ctx), request.headers.get("user-agent")
    )
    audit(db, user.id, "user", "auth.login", "session", session.id)
    db.commit()
    return {"session_token": token, "expires_at": session.expires_at, "mfa_required": False, "mfa_enrolled": False}
