"""Templates, receipt attachments, people you share expenses with, and dismissed alerts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Integer, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Timestamps, uuid_pk
from ..types import fk, owner_fk


class TransactionTemplate(Timestamps, Base):
    """A saved entry ("Milk", "Rent to landlord") for one-tap manual entry."""

    __tablename__ = "transaction_templates"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # expense | income | transfer
    account_id: Mapped[uuid.UUID | None] = fk("accounts.id", nullable=True, ondelete="CASCADE", index=False)
    to_account_id: Mapped[uuid.UUID | None] = fk("accounts.id", nullable=True, ondelete="CASCADE", index=False)
    amount: Mapped[Decimal | None]
    category_id: Mapped[uuid.UUID | None] = fk(
        "transaction_categories.id", nullable=True, ondelete="SET NULL", index=False
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_used_at: Mapped[datetime | None]


class TransactionAttachment(Base):
    """A receipt or photo attached to a transaction, encrypted with the application key."""

    __tablename__ = "transaction_attachments"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    transaction_id: Mapped[uuid.UUID] = fk("transactions.id", ondelete="CASCADE", index=False)
    filename: Mapped[str] = mapped_column(String(160), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class Person(Timestamps, Base):
    """Someone you share expenses with. What they owe you is the balance of their own account."""

    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("owner_id", "account_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    account_id: Mapped[uuid.UUID] = fk("accounts.id", index=False)
    archived_at: Mapped[datetime | None]


class AlertDismissal(Base):
    __tablename__ = "alert_dismissals"
    __table_args__ = (UniqueConstraint("owner_id", "alert_key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    alert_key: Mapped[str] = mapped_column(String(200), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
