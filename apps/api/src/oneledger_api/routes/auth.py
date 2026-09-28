"""Authentication, MFA, sessions and owner settings. There is no public registration endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from oneledger_db.models import AuthSession
from oneledger_shared.errors import AuthenticationError, ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..deps import DB, AdminAuth, Ctx, EnrolAuth, ReadAuth, client_key
from ..security.auth import login, mfa_required_for, new_totp_secret, totp_uri, verify_totp
from ..services.audit import audit

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    session_token: str
    expires_at: datetime
    mfa_required: bool
    mfa_enrolled: bool


@router.post("/auth/login", response_model=LoginOut)
def do_login(body: LoginIn, request: Request, ctx: Ctx, db: DB) -> LoginOut:
    token, session, user = login(
        ctx, db, body.email, body.password, client_key(request, ctx), request.headers.get("user-agent")
    )
    audit(db, user.id, "user", "auth.login", "session", session.id)
    db.commit()
    return LoginOut(
        session_token=token,
        expires_at=session.expires_at,
        mfa_required=mfa_required_for(ctx, user),
        mfa_enrolled=user.mfa_enabled,
    )


@router.post("/auth/logout", status_code=204)
def logout(a: EnrolAuth) -> None:
    a.db.execute(
        update(AuthSession).where(AuthSession.id == a.principal.credential_id).values(revoked_at=datetime.now(UTC))
    )
    audit(a.db, a.owner_id, a.actor, "auth.logout", "session", a.principal.credential_id)
    a.commit()


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.post("/auth/mfa/verify")
def mfa_verify(body: MfaCode, a: EnrolAuth) -> dict[str, bool]:
    user = a.user()
    if not user.mfa_enabled or user.totp_secret_enc is None:
        raise ValidationFailed("MFA is not enrolled.", code="MFA_NOT_ENROLLED")
    secret = a.ctx.box.decrypt_str(user.totp_secret_enc, associated_data=str(user.id).encode())
    if not verify_totp(secret, body.code):
        audit(a.db, a.owner_id, a.actor, "auth.mfa_failed", "session", a.principal.credential_id)
        a.commit()
        raise AuthenticationError("Invalid verification code.", code="MFA_INVALID")
    a.db.execute(
        update(AuthSession).where(AuthSession.id == a.principal.credential_id).values(mfa_verified_at=datetime.now(UTC))
    )
    audit(a.db, a.owner_id, a.actor, "auth.mfa_verified", "session", a.principal.credential_id)
    a.commit()
    return {"verified": True}


@router.post("/auth/mfa/enroll")
def mfa_enroll_start(a: EnrolAuth) -> dict[str, str]:
    """Begin TOTP enrolment. Allowed for a new session only while MFA is not yet enabled."""
    user = a.user()
    if user.mfa_enabled and not a.principal.mfa_satisfied:
        raise AuthenticationError("Verify your current MFA code first.", code="MFA_REQUIRED")
    secret = new_totp_secret()
    user.totp_secret_enc = a.ctx.box.encrypt_str(secret, associated_data=str(user.id).encode())
    user.mfa_enabled = False
    audit(a.db, a.owner_id, a.actor, "auth.mfa_enroll_start", "user", user.id, ["totp_secret"])
    a.commit()
    return {"otpauth_uri": totp_uri(secret, user.email), "secret": secret}


@router.post("/auth/mfa/enroll/confirm")
def mfa_enroll_confirm(body: MfaCode, a: EnrolAuth) -> dict[str, bool]:
    user = a.user()
    if user.totp_secret_enc is None:
        raise ValidationFailed("Start enrolment first.", code="MFA_NOT_STARTED")
    secret = a.ctx.box.decrypt_str(user.totp_secret_enc, associated_data=str(user.id).encode())
    if not verify_totp(secret, body.code):
        raise AuthenticationError("Invalid verification code.", code="MFA_INVALID")
    user.mfa_enabled = True
    a.db.execute(
        update(AuthSession).where(AuthSession.id == a.principal.credential_id).values(mfa_verified_at=datetime.now(UTC))
    )
    audit(a.db, a.owner_id, a.actor, "auth.mfa_enabled", "user", user.id, ["mfa_enabled"])
    a.commit()
    return {"enabled": True}


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    timezone: str
    base_currency: str
    mfa_enabled: bool
    mfa_required: bool
    mfa_satisfied: bool
    onboarded: bool
    credential: str


@router.get("/me", response_model=MeOut)
def me(a: EnrolAuth) -> MeOut:
    u = a.user()
    return MeOut(
        id=u.id,
        email=u.email if a.principal.kind == "session" else "",
        display_name=u.display_name,
        timezone=u.timezone,
        base_currency=u.base_currency,
        mfa_enabled=u.mfa_enabled,
        mfa_required=mfa_required_for(a.ctx, u),
        mfa_satisfied=a.principal.mfa_satisfied,
        onboarded=u.onboarded_at is not None,
        credential=a.principal.kind,
    )


class SettingsIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    onboarded: bool | None = None


@router.patch("/me", response_model=MeOut)
def update_me(body: SettingsIn, a: AdminAuth) -> MeOut:
    u = a.user()
    changed = []
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationFailed("Unknown timezone.", code="INVALID_TIMEZONE") from exc
        u.timezone = body.timezone
        changed.append("timezone")
    if body.display_name is not None:
        u.display_name = body.display_name.strip()
        changed.append("display_name")
    if body.base_currency is not None:
        u.base_currency = body.base_currency.upper()
        changed.append("base_currency")
    if body.onboarded and u.onboarded_at is None:
        u.onboarded_at = datetime.now(UTC)
        changed.append("onboarded_at")
    u.version += 1
    audit(a.db, a.owner_id, a.actor, "user.update", "user", u.id, changed)
    a.commit()
    return me(a)


@router.get("/auth/sessions")
def list_sessions(a: ReadAuth) -> list[dict[str, object]]:
    if a.principal.kind != "session":
        return []
    now = datetime.now(UTC)
    rows = a.db.scalars(
        select(AuthSession)
        .where(AuthSession.owner_id == a.owner_id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
        .order_by(AuthSession.last_seen_at.desc())
    ).all()
    return [
        {
            "id": str(s.id),
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "expires_at": s.expires_at,
            "user_agent": s.user_agent,
            "current": s.id == a.principal.credential_id,
            "mfa_verified": s.mfa_verified_at is not None,
        }
        for s in rows
    ]


@router.post("/auth/sessions/revoke-others")
def revoke_other_sessions(a: AdminAuth) -> dict[str, int]:
    """Sign out every device except the one making this request."""
    current = a.principal.credential_id if a.principal.kind == "session" else None
    result = a.db.execute(
        update(AuthSession)
        .where(AuthSession.owner_id == a.owner_id, AuthSession.revoked_at.is_(None), AuthSession.id != current)
        .values(revoked_at=datetime.now(UTC))
    )
    audit(a.db, a.owner_id, a.actor, "auth.session_revoke_others", "session", None)
    a.commit()
    return {"signed_out": int(getattr(result, "rowcount", 0) or 0)}


@router.delete("/auth/sessions/{session_id}", status_code=204)
def revoke_session(session_id: uuid.UUID, a: AdminAuth) -> None:
    a.db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.owner_id == a.owner_id)
        .values(revoked_at=datetime.now(UTC))
    )
    audit(a.db, a.owner_id, a.actor, "auth.session_revoke", "session", session_id)
    a.commit()
