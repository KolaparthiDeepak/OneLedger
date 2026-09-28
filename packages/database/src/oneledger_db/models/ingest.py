"""Imports and durable jobs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from oneledger_domain.enums import ImportRowResolution, ImportRowStatus, ImportState, JobStatus
from sqlalchemy import (
    Boolean,
    Date,
    ForeignKeyConstraint,
    Index,
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
from ..types import enum_type, fk, owner_fk


class ImportFile(Base):
    """Uploaded statement bytes, encrypted with the application key. Purged after retention."""

    __tablename__ = "import_files"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_format: Mapped[str] = mapped_column(String(10), nullable=False)
    content_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    purged_at: Mapped[datetime | None]


class Import(Timestamps, Base):
    __tablename__ = "imports"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "account_id"], ["accounts.owner_id", "accounts.id"]),
        Index("ix_imports_replay", "owner_id", "account_id", "file_sha256", "mapping_version", "parser_version"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    file_id: Mapped[uuid.UUID] = fk("import_files.id")
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[ImportState] = mapped_column(enum_type(ImportState, "import_state"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_stage: Mapped[str | None] = mapped_column(String(30))
    error_message: Mapped[str | None] = mapped_column(String(300))
    headers: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False, server_default="{}")
    suggested_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    mapping_hash: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    preview_hash: Mapped[str | None] = mapped_column(String(64))
    preview_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    date_min: Mapped[date | None] = mapped_column(Date)
    date_max: Mapped[date | None] = mapped_column(Date)
    replay_of_id: Mapped[uuid.UUID | None] = fk("imports.id", nullable=True)
    confirmed_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    job_id: Mapped[uuid.UUID | None] = fk("jobs.id", nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (UniqueConstraint("import_id", "row_index"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    import_id: Mapped[uuid.UUID] = fk("imports.id", ondelete="CASCADE")
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    transaction_date: Mapped[date | None] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None]
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    reference: Mapped[str | None] = mapped_column(String(64))
    balance_after: Mapped[Decimal | None]
    source_drcr: Mapped[str | None] = mapped_column(String(2))
    channel: Mapped[str] = mapped_column(String(20), nullable=False, server_default="OTHER")
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ImportRowStatus] = mapped_column(enum_type(ImportRowStatus, "import_row_status"), nullable=False)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    errors: Mapped[list[str]] = mapped_column(ARRAY(String(60)), nullable=False, server_default="{}")
    dedup_reason: Mapped[str | None] = mapped_column(String(60))
    duplicate_of_transaction_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    resolution: Mapped[ImportRowResolution | None] = mapped_column(
        enum_type(ImportRowResolution, "import_row_resolution")
    )
    transaction_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    committed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class Job(Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_ready", "status", "next_attempt_at"),
        UniqueConstraint("owner_id", "idempotency_key"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[JobStatus] = mapped_column(enum_type(JobStatus, "job_status"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")  # IDs only
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_expires_at: Mapped[datetime | None]
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    completed_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
