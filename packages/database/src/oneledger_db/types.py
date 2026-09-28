"""Column helpers."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


def enum_type(e: type[StrEnum], name: str) -> Enum:
    return Enum(
        e,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        values_callable=lambda cls: [m.value for m in cls],
        validate_strings=True,
    )


def owner_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)


def fk(
    target: str, *, nullable: bool = False, ondelete: str = "RESTRICT", index: bool = True, **kw: Any
) -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index, **kw
    )
