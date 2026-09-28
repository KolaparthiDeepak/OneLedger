"""Loans, credit cards, investments, cash, recurrence, budgets, goals, net worth, anomalies."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from oneledger_domain.enums import InstrumentType, InvestmentAction, RecurrenceCadence, RecurrenceType
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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

from ..base import Base, Quantity, Rate, Timestamps, uuid_pk
from ..types import enum_type, fk, owner_fk


class Loan(Timestamps, Base):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint("original_principal > 0", name="positive_principal"),
        CheckConstraint("opening_outstanding >= 0", name="nonnegative_outstanding"),
        CheckConstraint("tenure_months > 0", name="positive_tenure"),
        CheckConstraint("emi_day BETWEEN 1 AND 31", name="emi_day_range"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID] = fk("accounts.id", unique=True)
    lender: Mapped[str] = mapped_column(String(120), nullable=False)
    loan_type: Mapped[str] = mapped_column(String(40), nullable=False)
    original_principal: Mapped[Decimal] = mapped_column(nullable=False)
    opening_outstanding: Mapped[Decimal] = mapped_column(nullable=False)
    opening_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_emi_date: Mapped[date] = mapped_column(Date, nullable=False)
    tenure_months: Mapped[int] = mapped_column(Integer, nullable=False)
    emi_amount: Mapped[Decimal] = mapped_column(nullable=False)
    emi_day: Mapped[int] = mapped_column(Integer, nullable=False)
    interest_convention: Mapped[str] = mapped_column(String(40), nullable=False, server_default="MONTHLY_REDUCING")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class LoanRateChange(Base):
    __tablename__ = "loan_rate_changes"
    __table_args__ = (
        UniqueConstraint("loan_id", "effective_date"),
        CheckConstraint("annual_rate_percent >= 0", name="nonnegative_rate"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    loan_id: Mapped[uuid.UUID] = fk("loans.id", ondelete="CASCADE")
    annual_rate_percent: Mapped[Decimal] = mapped_column(Rate, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class LoanPayment(Timestamps, Base):
    __tablename__ = "loan_payments"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    loan_id: Mapped[uuid.UUID] = fk("loans.id")
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    transaction_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    principal: Mapped[Decimal] = mapped_column(nullable=False)
    interest: Mapped[Decimal] = mapped_column(nullable=False)
    fees: Mapped[Decimal] = mapped_column(nullable=False, server_default="0")
    prepayment: Mapped[Decimal] = mapped_column(nullable=False, server_default="0")
    is_actual: Mapped[bool] = mapped_column(Boolean, nullable=False)  # lender-reported vs estimated
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    deleted_at: Mapped[datetime | None]


class CreditCard(Timestamps, Base):
    __tablename__ = "credit_cards"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID] = fk("accounts.id", unique=True)
    issuer: Mapped[str] = mapped_column(String(120), nullable=False)
    network: Mapped[str | None] = mapped_column(String(30))
    credit_limit: Mapped[Decimal | None]
    statement_day: Mapped[int | None] = mapped_column(Integer)
    payment_due_days: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class CreditCardStatement(Timestamps, Base):
    __tablename__ = "credit_card_statements"
    __table_args__ = (UniqueConstraint("card_id", "period_end"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    card_id: Mapped[uuid.UUID] = fk("credit_cards.id")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    statement_balance: Mapped[Decimal] = mapped_column(nullable=False)
    minimum_due: Mapped[Decimal | None]
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="MANUAL")


class Investment(Timestamps, Base):
    __tablename__ = "investments"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID | None] = fk("accounts.id", nullable=True)
    instrument_type: Mapped[InstrumentType] = mapped_column(
        enum_type(InstrumentType, "instrument_type"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(40))  # ISIN / scheme code / ticker
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    valuation_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # UNITS | MANUAL_TOTAL
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class InvestmentTransaction(Timestamps, Base):
    __tablename__ = "investment_transactions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    investment_id: Mapped[uuid.UUID] = fk("investments.id")
    transaction_id: Mapped[uuid.UUID | None] = fk("transactions.id", nullable=True)
    action: Mapped[InvestmentAction] = mapped_column(enum_type(InvestmentAction, "investment_action"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    units: Mapped[Decimal | None] = mapped_column(Quantity)
    unit_price: Mapped[Decimal | None] = mapped_column(Quantity)
    gross_amount: Mapped[Decimal] = mapped_column(nullable=False)
    fees: Mapped[Decimal] = mapped_column(nullable=False, server_default="0")
    deleted_at: Mapped[datetime | None]


class InvestmentValuation(Base):
    __tablename__ = "investment_valuations"
    __table_args__ = (Index("ix_investment_valuations_asof", "investment_id", "valuation_date"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    investment_id: Mapped[uuid.UUID] = fk("investments.id")
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Quantity)
    total_value: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class CashAccount(Timestamps, Base):
    __tablename__ = "cash_accounts"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    account_id: Mapped[uuid.UUID] = fk("accounts.id", unique=True)
    notes: Mapped[str | None] = mapped_column(Text)


class RecurringTransaction(Timestamps, Base):
    __tablename__ = "recurring_transactions"
    __table_args__ = (UniqueConstraint("owner_id", "pattern_key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    pattern_key: Mapped[str] = mapped_column(String(300), nullable=False)
    account_id: Mapped[uuid.UUID] = fk("accounts.id")
    merchant_key: Mapped[str] = mapped_column(String(160), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[uuid.UUID | None] = fk("transaction_categories.id", nullable=True)
    recurrence_type: Mapped[RecurrenceType] = mapped_column(
        enum_type(RecurrenceType, "recurrence_type"), nullable=False
    )
    cadence: Mapped[RecurrenceCadence] = mapped_column(
        enum_type(RecurrenceCadence, "recurrence_cadence"), nullable=False
    )
    typical_amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_variable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_next_date: Mapped[date] = mapped_column(Date, nullable=False)
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="SUGGESTED")  # ACCEPTED|DISMISSED
    detection_version: Mapped[str] = mapped_column(String(30), nullable=False)


class RecurringTransactionMember(Base):
    __tablename__ = "recurring_transaction_members"
    __table_args__ = (UniqueConstraint("recurring_id", "transaction_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    recurring_id: Mapped[uuid.UUID] = fk("recurring_transactions.id", ondelete="CASCADE")
    transaction_id: Mapped[uuid.UUID] = fk("transactions.id")


class Budget(Timestamps, Base):
    __tablename__ = "budgets"
    __table_args__ = (CheckConstraint("amount > 0", name="positive_amount"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category_id: Mapped[uuid.UUID | None] = fk("transaction_categories.id", nullable=True)
    account_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False, server_default="MONTHLY")
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    rollover: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class FinancialGoal(Timestamps, Base):
    __tablename__ = "financial_goals"
    __table_args__ = (CheckConstraint("target_amount > 0", name="positive_target"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date)
    progress_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # ACCOUNTS | MANUAL
    account_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    deleted_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class GoalContribution(Base):
    __tablename__ = "goal_contributions"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    goal_id: Mapped[uuid.UUID] = fk("financial_goals.id", ondelete="CASCADE")
    contribution_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class NetWorthSnapshot(Base):
    __tablename__ = "net_worth_snapshots"
    __table_args__ = (Index("ix_net_worth_snapshots_date", "owner_id", "snapshot_date", "currency"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    assets: Mapped[Decimal] = mapped_column(nullable=False)
    liabilities: Mapped[Decimal] = mapped_column(nullable=False)
    net_worth: Mapped[Decimal] = mapped_column(nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False, server_default="{}")
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    calculation_version: Mapped[str] = mapped_column(String(30), nullable=False)
    ledger_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (UniqueConstraint("owner_id", "rule", "transaction_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = owner_fk()
    rule: Mapped[str] = mapped_column(String(60), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(30), nullable=False)
    transaction_id: Mapped[uuid.UUID] = fk("transactions.id")
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    detected_at: Mapped[datetime] = mapped_column(server_default=text("now()"), nullable=False)
    dismissed_at: Mapped[datetime | None]
