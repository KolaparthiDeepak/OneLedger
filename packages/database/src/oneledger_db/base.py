"""Declarative base, naming conventions and common column mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, MetaData, Numeric, TypeDecorator, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "oneledger"

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class MoneyType(TypeDecorator[Decimal]):
    """NUMERIC(24,8) storage; values read back at 2 decimal places when that is lossless."""

    impl = Numeric(24, 8)
    cache_ok = True

    def process_result_value(self, value: Decimal | None, dialect: object) -> Decimal | None:
        if value is None:
            return None
        two = value.quantize(_CENT)
        return two if two == value else value.normalize()


_CENT = Decimal("0.01")
Money = MoneyType()
Rate = Numeric(12, 6)
Quantity = Numeric(28, 10)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING)
    type_annotation_map = {Decimal: Money, uuid.UUID: UUID(as_uuid=True), datetime: DateTime(timezone=True)}


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)
