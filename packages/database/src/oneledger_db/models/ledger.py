"""Unified ledger: accounts, balances, transactions, allocations, categories, transfers, review."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from oneledger_domain.enums import (
    AccountKind,
    AccountStatus,
    AllocationEffect,
    BalanceKind,
    BalanceSource,
    ClassificationSource,
    PaymentChannel,
    RelationKind,
    ReviewKind,
    ReviewStatus,
    TransactionSourceKind,
    TransactionStatus,
    TransferStatus,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Timestamps, uuid_pk
from ..types import enum_type, fk, owner_fk


class FinancialInstitution(Timestamps, Base):
    __tablename__ = "financial_institutions"
    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class Account(Timestamps, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_accounts_owner_id_id"),
        CheckConstraint("char_length(currency) = 3", name="currency_len"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    institution_id: Mapped[uuid.UUID | None] = fk("financial_institutions.id", nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[AccountKind] = mapped_column(enum_type(AccountKind, "account_kind"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    masked_identifier: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[AccountStatus] = mapped_column(
        enum_type(AccountStatus, "account_status"), nullable=False, server_default=AccountStatus.ACTIVE.value
    )
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    opening_date: Mapped[date | None] = mapped_column(Date)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "account_id"], ["accounts.owner_id", "accounts.id"]),
        Index("ix_balance_snapshots_account_asof", "owner_id", "account_id", "as_of"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)  # natural balance
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_kind: Mapped[BalanceKind] = mapped_column(enum_type(BalanceKind, "balance_kind"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_time: Mapped[datetime | None]
    source: Mapped[BalanceSource] = mapped_column(enum_type(BalanceSource, "balance_source"), nullable=False)
    is_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    source_ref: Mapped[str | None] = mapped_column(String(80))
    reconciliation_state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="UNCHECKED")
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class TransactionCategory(Timestamps, Base):
    __tablename__ = "transaction_categories"
    __table_args__ = (
        UniqueConstraint("owner_id", "code"),
        UniqueConstraint("owner_id", "id", name="uq_transaction_categories_owner_id_id"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="no_self_parent"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = fk("transaction_categories.id", nullable=True)
    default_effect: Mapped[AllocationEffect] = mapped_column(
        enum_type(AllocationEffect, "allocation_effect"), nullable=False
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    archived_at: Mapped[datetime | None]


class TransactionMerchant(Timestamps, Base):
    __tablename__ = "transaction_merchants"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(120)), nullable=False, server_default="{}")
    default_category_id: Mapped[uuid.UUID | None] = fk("transaction_categories.id", nullable=True)


class Transaction(Timestamps, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_transactions_owner_id_id"),
        ForeignKeyConstraint(["owner_id", "account_id"], ["accounts.owner_id", "accounts.id"]),
        Index("ix_transactions_owner_date", "owner_id", text("transaction_date DESC"), text("id DESC")),
        Index("ix_transactions_owner_account_date", "owner_id", "account_id", "transaction_date"),
        Index("ix_transactions_fingerprint", "owner_id", "account_id", "fingerprint"),
        Index("ix_transactions_reference", "owner_id", "account_id", "reference"),
        CheckConstraint("amount <> 0", name="nonzero_amount"),
        CheckConstraint("char_length(currency) = 3", name="currency_len"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_date: Mapped[date | None] = mapped_column(Date)
    occurred_at: Mapped[datetime | None]
    status: Mapped[TransactionStatus] = mapped_column(
        enum_type(TransactionStatus, "transaction_status"),
        nullable=False,
        server_default=TransactionStatus.POSTED.value,
    )
    movement_type: Mapped[str | None] = mapped_column(String(40))
    payment_channel: Mapped[PaymentChannel] = mapped_column(
        enum_type(PaymentChannel, "payment_channel"), nullable=False, server_default=PaymentChannel.OTHER.value
    )
    source_drcr: Mapped[str | None] = mapped_column(String(2))
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_description: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_id: Mapped[uuid.UUID | None] = fk("transaction_merchants.id", nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(120))
    reference: Mapped[str | None] = mapped_column(String(64))
    balance_after: Mapped[Decimal | None]
    source: Mapped[TransactionSourceKind] = mapped_column(
        enum_type(TransactionSourceKind, "transaction_source"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    import_id: Mapped[uuid.UUID | None] = fk("imports.id", nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    merged_into_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    pending_of_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class TransactionSource(Base):
    __tablename__ = "transaction_sources"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "transaction_id"], ["transactions.owner_id", "transactions.id"]),
        Index(
            "uq_transaction_sources_provider_identity",
            "owner_id",
            "provider_namespace",
            "account_id",
            "source_transaction_id",
            unique=True,
            postgresql_where=text("source_transaction_id IS NOT NULL AND is_active"),
        ),
        Index(
            "uq_transaction_sources_import_row",
            "import_row_id",
            unique=True,
            postgresql_where=text("import_row_id IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = fk("accounts.id")
    provider_namespace: Mapped[str] = mapped_column(String(40), nullable=False)
    source_transaction_id: Mapped[str | None] = mapped_column(String(128))
    import_row_id: Mapped[uuid.UUID | None] = fk("import_rows.id", nullable=True, index=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class TransactionAllocation(Timestamps, Base):
    __tablename__ = "transaction_allocations"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_transaction_allocations_owner_id_id"),
        ForeignKeyConstraint(
            ["owner_id", "transaction_id"], ["transactions.owner_id", "transactions.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["owner_id", "category_id"], ["transaction_categories.owner_id", "transaction_categories.id"]
        ),
        Index("ix_allocations_effect", "owner_id", "effect"),
        CheckConstraint("amount <> 0", name="nonzero_amount"),
        CheckConstraint(
            "classification_confidence IS NULL OR classification_confidence BETWEEN 0 AND 1", name="confidence_range"
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    effect: Mapped[AllocationEffect] = mapped_column(enum_type(AllocationEffect, "allocation_effect"), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    classification_source: Mapped[ClassificationSource] = mapped_column(
        enum_type(ClassificationSource, "classification_source"), nullable=False
    )
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    rule_id: Mapped[uuid.UUID | None] = fk("transaction_category_rules.id", nullable=True, ondelete="SET NULL")
    rule_version: Mapped[int | None]
    model: Mapped[str | None] = mapped_column(String(80))
    model_version: Mapped[str | None] = mapped_column(String(40))
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    related_transaction_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    loan_id: Mapped[uuid.UUID | None] = fk("loans.id", nullable=True)
    investment_id: Mapped[uuid.UUID | None] = fk("investments.id", nullable=True)
    note: Mapped[str | None] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class TransactionCategoryRule(Timestamps, Base):
    __tablename__ = "transaction_category_rules"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # validated RuleSpec
    target_category_id: Mapped[uuid.UUID] = fk("transaction_categories.id")
    target_effect: Mapped[AllocationEffect] = mapped_column(
        enum_type(AllocationEffect, "allocation_effect"), nullable=False
    )
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class TransactionTag(Timestamps, Base):
    __tablename__ = "transaction_tags"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))


class TransactionTagLink(Base):
    __tablename__ = "transaction_tag_links"
    __table_args__ = (UniqueConstraint("transaction_id", "tag_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    transaction_id: Mapped[uuid.UUID] = fk("transactions.id", ondelete="CASCADE")
    tag_id: Mapped[uuid.UUID] = fk("transaction_tags.id", ondelete="CASCADE")
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class Transfer(Timestamps, Base):
    __tablename__ = "transfers"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    status: Mapped[TransferStatus] = mapped_column(enum_type(TransferStatus, "transfer_status"), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # AUTO | MANUAL
    reason: Mapped[str] = mapped_column(String(60), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    confirmed_at: Mapped[datetime | None]
    decided_by: Mapped[str | None] = mapped_column(String(20))  # user | system


class TransferLeg(Base):
    __tablename__ = "transfer_legs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "allocation_id"],
            ["transaction_allocations.owner_id", "transaction_allocations.id"],
            ondelete="CASCADE",
        ),
        Index("uq_transfer_legs_active_allocation", "allocation_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    transfer_id: Mapped[uuid.UUID] = fk("transfers.id", ondelete="CASCADE")
    allocation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = fk("transactions.id")
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class TransactionRelation(Base):
    __tablename__ = "transaction_relations"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    kind: Mapped[RelationKind] = mapped_column(enum_type(RelationKind, "relation_kind"), nullable=False)
    from_transaction_id: Mapped[uuid.UUID] = fk("transactions.id")
    to_transaction_id: Mapped[uuid.UUID] = fk("transactions.id")
    amount: Mapped[Decimal | None]
    reason: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    undone_at: Mapped[datetime | None]


class TransactionRevision(Base):
    __tablename__ = "transaction_revisions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    transaction_id: Mapped[uuid.UUID] = fk("transactions.id")
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    actor: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class ReviewItem(Timestamps, Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index(
            "uq_review_items_open_key", "owner_id", "dedupe_key", unique=True, postgresql_where=text("status = 'OPEN'")
        ),
        Index("ix_review_items_status", "owner_id", "status", "kind"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    kind: Mapped[ReviewKind] = mapped_column(enum_type(ReviewKind, "review_kind"), nullable=False)
    status: Mapped[ReviewStatus] = mapped_column(
        enum_type(ReviewStatus, "review_status"), nullable=False, server_default=ReviewStatus.OPEN.value
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    transaction_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    related: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(60))
    resolved_at: Mapped[datetime | None]


class LedgerState(Base):
    """Monotonic ledger revision per owner, used for report provenance."""

    __tablename__ = "ledger_state"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class ReportQuery(Base):
    """Owner-scoped normalized query behind a report's ``query_id`` for reproducible evidence."""

    __tablename__ = "report_queries"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
