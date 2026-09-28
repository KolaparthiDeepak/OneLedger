"""Identity, sessions, tokens, audit, AI settings and request bookkeeping."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Timestamps, uuid_pk
from ..types import fk, owner_fk


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Asia/Kolkata")
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    is_owner_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    onboarded_at: Mapped[datetime | None]
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    mfa_verified_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(String(200))


class Invite(Base):
    """A one-time link that lets someone create their own, separate ledger on this server."""

    __tablename__ = "invites"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()  # who sent it
    email: Mapped[str | None] = mapped_column(String(320))  # if set, only this email can use it
    note: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]
    used_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    revoked_at: Mapped[datetime | None]


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    actor: Mapped[str] = mapped_column(String(40), nullable=False)  # user | system | mcp:<token-id> | worker
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID | None]
    changed_fields: Mapped[list[str]] = mapped_column(ARRAY(String(60)), nullable=False, server_default="{}")
    revision_id: Mapped[uuid.UUID | None]
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False, index=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("owner_id", "key", "route"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class RateLimitCounter(Base):
    """Shared-state fixed-window rate limiting (works across serverless instances)."""

    __tablename__ = "rate_limit_counters"
    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class UserSecret(Timestamps, Base):
    __tablename__ = "user_secrets"
    __table_args__ = (UniqueConstraint("owner_id", "kind"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. ai_api_key
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    masked_suffix: Mapped[str] = mapped_column(String(8), nullable=False)


class AiSettings(Timestamps, Base):
    __tablename__ = "ai_settings"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, server_default="openrouter")
    model: Mapped[str] = mapped_column(String(80), nullable=False, server_default="anthropic/claude-opus-5")
    assistant_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    classification_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    share_descriptions: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    auto_categorize: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Why automatic categorisation last stopped (cleared once a run or connection test succeeds).
    auto_error_code: Mapped[str | None] = mapped_column(String(60))
    auto_error_message: Mapped[str | None] = mapped_column(String(300))
    auto_error_at: Mapped[datetime | None]
    opted_in_at: Mapped[datetime | None]
    policy_version: Mapped[str | None] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class AiRun(Base):
    __tablename__ = "ai_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # assistant | classification | test
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    tool_names: Mapped[list[str]] = mapped_column(ARRAY(String(60)), nullable=False, server_default="{}")
    query_ids: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, server_default="{}")
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False, index=True)
