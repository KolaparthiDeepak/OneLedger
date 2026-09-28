"""Owner authentication: argon2id passwords, TOTP MFA, opaque DB-backed sessions, scoped API tokens.

Tokens are high-entropy random strings; only an HMAC-SHA256 keyed hash is stored. Sessions are
revocable server-side and expire on both absolute TTL and idle timeout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from oneledger_db.models import ApiToken, AuthSession, User
from oneledger_db.session import set_owner
from oneledger_shared.config import AppEnv
from oneledger_shared.crypto import new_token
from oneledger_shared.errors import AuthenticationError, PermissionDenied, RateLimited
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from ..context import AppContext
from .ratelimit import hit

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
# Verified against when the email is unknown, so timing does not reveal account existence.
_DUMMY_HASH = _hasher.hash("oneledger-dummy-password-for-timing")

SESSION_PREFIX = "ols"
API_TOKEN_PREFIX = "olt"  # noqa: S105 - a public prefix, not a secret
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)

SCOPE_READ = "finance:read"
SCOPE_READ_DETAIL = "transactions:read-detail"
SCOPE_WRITE = "finance:write"
SCOPE_ADMIN = "owner:admin"
MCP_ALLOWED_SCOPES = frozenset({SCOPE_READ, SCOPE_READ_DETAIL})


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


@dataclass(frozen=True)
class Principal:
    owner_id: uuid.UUID
    kind: str  # "session" | "api_token"
    scopes: frozenset[str]
    credential_id: uuid.UUID
    mfa_satisfied: bool = True
    mfa_pending: bool = False
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def actor(self) -> str:
        return "user" if self.kind == "session" else f"token:{self.credential_id}"

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise PermissionDenied("This credential does not permit that operation.", code="INSUFFICIENT_SCOPE")
        if self.mfa_pending:
            raise AuthenticationError("Complete multi-factor verification first.", code="MFA_REQUIRED")

    def require_strong(self) -> None:
        """Sensitive actions: secrets, full export, token issuance."""
        self.require(SCOPE_ADMIN)
        if not self.mfa_satisfied:
            raise PermissionDenied("Multi-factor authentication is required for this action.", code="MFA_REQUIRED")


def _now() -> datetime:
    return datetime.now(UTC)


def login(
    ctx: AppContext, db: Session, email: str, password: str, client_key: str, user_agent: str | None
) -> tuple[str, AuthSession, User]:
    email = email.strip().lower()
    if not hit(db, f"login:ip:{client_key}", limit=20, window_seconds=900) or not hit(
        db, f"login:email:{ctx.token_hash(email)}", limit=10, window_seconds=900
    ):
        db.commit()
        raise RateLimited("Too many login attempts. Try again later.")
    db.commit()
    row = db.execute(text("SELECT * FROM oneledger.auth_user_by_email(:e)"), {"e": email}).mappings().first()
    if row is None:
        verify_password(_DUMMY_HASH, password)
        no_owner = not db.execute(text("SELECT * FROM oneledger.enabled_owner_ids()")).first()
        if ctx.settings.app_env == AppEnv.LOCAL and no_owner:
            # Local setup help only: never revealed in test/preview/production.
            raise AuthenticationError(
                "No owner account exists yet. Create one in a terminal: make owner EMAIL=you@example.com",
                code="NO_OWNER",
            )
        raise AuthenticationError("Invalid email or password.", code="INVALID_CREDENTIALS")
    owner_id: uuid.UUID = row["id"]
    set_owner(db, owner_id)
    user = db.get(User, owner_id)
    assert user is not None
    now = _now()
    if user.locked_until and user.locked_until > now:
        raise AuthenticationError("Account temporarily locked after failed attempts.", code="ACCOUNT_LOCKED")
    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now + LOCKOUT
            user.failed_logins = 0
        db.commit()
        raise AuthenticationError("Invalid email or password.", code="INVALID_CREDENTIALS")
    if not user.is_owner_enabled:
        raise AuthenticationError("This account is not enabled.", code="OWNER_DISABLED")
    user.failed_logins = 0
    user.locked_until = None
    token = new_token(SESSION_PREFIX)
    session = AuthSession(
        owner_id=owner_id,
        token_hash=ctx.token_hash(token),
        expires_at=now + timedelta(hours=ctx.settings.session_ttl_hours),
        last_seen_at=now,
        mfa_verified_at=None,
        user_agent=(user_agent or "")[:200] or None,
    )
    db.add(session)
    db.flush()
    return token, session, user


def mfa_required_for(ctx: AppContext, user: User) -> bool:
    return user.mfa_enabled or ctx.settings.require_mfa


def authenticate(ctx: AppContext, db: Session, bearer: str) -> Principal:
    """Resolve a bearer credential and bind the DB session to its owner."""
    if bearer.startswith(f"{SESSION_PREFIX}_"):
        return _authenticate_session(ctx, db, bearer)
    if bearer.startswith(f"{API_TOKEN_PREFIX}_"):
        return _authenticate_token(ctx, db, bearer)
    raise AuthenticationError()


def _authenticate_session(ctx: AppContext, db: Session, token: str) -> Principal:
    row = (
        db.execute(text("SELECT * FROM oneledger.auth_session_lookup(:h)"), {"h": ctx.token_hash(token)})
        .mappings()
        .first()
    )
    now = _now()
    if row is None or row["revoked_at"] is not None or row["expires_at"] <= now:
        raise AuthenticationError(code="SESSION_INVALID")
    if row["last_seen_at"] + timedelta(minutes=ctx.settings.session_idle_minutes) <= now:
        raise AuthenticationError("Session expired due to inactivity.", code="SESSION_IDLE_TIMEOUT")
    set_owner(db, row["owner_id"])
    user = db.get(User, row["owner_id"])
    if user is None or not user.is_owner_enabled:
        raise AuthenticationError(code="OWNER_DISABLED")
    if (now - row["last_seen_at"]).total_seconds() > 60:
        db.execute(update(AuthSession).where(AuthSession.id == row["session_id"]).values(last_seen_at=now))
    mfa_needed = mfa_required_for(ctx, user)
    mfa_ok = (not mfa_needed) or row["mfa_verified_at"] is not None
    # A session for a user who must enrol MFA but has not yet may only reach enrolment routes.
    mfa_pending = mfa_needed and not mfa_ok
    return Principal(
        owner_id=row["owner_id"],
        kind="session",
        scopes=frozenset({SCOPE_READ, SCOPE_READ_DETAIL, SCOPE_WRITE, SCOPE_ADMIN}),
        credential_id=row["session_id"],
        mfa_satisfied=mfa_ok,
        mfa_pending=mfa_pending,
    )


def _authenticate_token(ctx: AppContext, db: Session, token: str) -> Principal:
    row = (
        db.execute(text("SELECT * FROM oneledger.auth_api_token_lookup(:h)"), {"h": ctx.token_hash(token)})
        .mappings()
        .first()
    )
    now = _now()
    if row is None or row["revoked_at"] is not None or row["expires_at"] <= now:
        raise AuthenticationError(code="TOKEN_INVALID")
    set_owner(db, row["owner_id"])
    user = db.get(User, row["owner_id"])
    if user is None or not user.is_owner_enabled:
        raise AuthenticationError(code="OWNER_DISABLED")
    db.execute(update(ApiToken).where(ApiToken.id == row["token_id"]).values(last_used_at=now))
    scopes = frozenset(s for s in row["scopes"] if s in MCP_ALLOWED_SCOPES)
    return Principal(owner_id=row["owner_id"], kind="api_token", scopes=scopes, credential_id=row["token_id"])


def issue_api_token(
    ctx: AppContext, db: Session, owner_id: uuid.UUID, label: str, scopes: list[str], days: int
) -> tuple[str, ApiToken]:
    bad = set(scopes) - MCP_ALLOWED_SCOPES
    if bad or SCOPE_READ not in scopes:
        raise PermissionDenied("Only read scopes can be issued to API tokens.", code="SCOPE_NOT_ALLOWED")
    if not 1 <= days <= 365:
        raise PermissionDenied("Token lifetime must be between 1 and 365 days.", code="INVALID_EXPIRY")
    if not hit(db, f"token-issue:{owner_id}", limit=10, window_seconds=3600):
        raise RateLimited("Too many tokens issued recently.")
    token = new_token(API_TOKEN_PREFIX)
    row = ApiToken(
        owner_id=owner_id,
        label=label[:80],
        token_hash=ctx.token_hash(token),
        token_prefix=token[:10],
        scopes=sorted(set(scopes)),
        expires_at=_now() + timedelta(days=days),
    )
    db.add(row)
    db.flush()
    return token, row


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    code = code.strip().replace(" ", "")
    return code.isdigit() and len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="OneLedger")
