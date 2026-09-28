"""Typed user rules. Conditions are data, never executable code or unrestricted regex."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from oneledger_domain.enums import AllocationEffect, PaymentChannel
from oneledger_domain.text import normalize_description
from pydantic import BaseModel, ConfigDict, Field, model_validator

TextField = Literal["description", "merchant"]


class TextCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: TextField
    op: Literal["contains", "starts_with", "equals"]
    value: str = Field(min_length=1, max_length=200)


class AmountCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["amount"] = "amount"
    op: Literal["gte", "lte", "between", "equals"]
    value: Decimal
    value_to: Decimal | None = None
    absolute: bool = True

    @model_validator(mode="after")
    def _between(self) -> AmountCondition:
        if self.op == "between" and (self.value_to is None or self.value_to < self.value):
            raise ValueError("between requires value_to >= value")
        return self


class AccountCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["account"] = "account"
    op: Literal["in"] = "in"
    value: list[UUID] = Field(min_length=1, max_length=50)


class ChannelCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["channel"] = "channel"
    op: Literal["in"] = "in"
    value: list[PaymentChannel] = Field(min_length=1)


class DirectionCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["direction"] = "direction"
    op: Literal["equals"] = "equals"
    value: Literal["debit", "credit"]


Condition = TextCondition | AmountCondition | AccountCondition | ChannelCondition | DirectionCondition


class RuleSpec(BaseModel):
    """All conditions must match (logical AND)."""

    model_config = ConfigDict(extra="forbid")
    conditions: list[Condition] = Field(min_length=1, max_length=10)
    category_code: str
    effect: AllocationEffect
    merchant_name: str | None = Field(default=None, max_length=120)


class TxnView(BaseModel):
    """Fields a rule may inspect."""

    model_config = ConfigDict(frozen=True)
    account_id: UUID
    amount: Decimal
    description: str
    merchant: str | None = None
    channel: PaymentChannel = PaymentChannel.OTHER


def _text(cond: TextCondition, txn: TxnView) -> bool:
    haystack = normalize_description(txn.description if cond.field == "description" else (txn.merchant or ""))
    needle = normalize_description(cond.value)
    if not needle:
        return False
    if cond.op == "contains":
        return f" {needle} " in f" {haystack} " or needle in haystack
    if cond.op == "starts_with":
        return haystack.startswith(needle)
    return haystack == needle


def condition_matches(cond: Condition, txn: TxnView) -> bool:
    if isinstance(cond, TextCondition):
        return _text(cond, txn)
    if isinstance(cond, AmountCondition):
        amt = abs(txn.amount) if cond.absolute else txn.amount
        if cond.op == "gte":
            return amt >= cond.value
        if cond.op == "lte":
            return amt <= cond.value
        if cond.op == "equals":
            return amt == cond.value
        assert cond.value_to is not None
        return cond.value <= amt <= cond.value_to
    if isinstance(cond, AccountCondition):
        return txn.account_id in cond.value
    if isinstance(cond, ChannelCondition):
        return txn.channel in cond.value
    return (txn.amount < 0) == (cond.value == "debit")


def rule_matches(spec: RuleSpec, txn: TxnView) -> bool:
    return all(condition_matches(c, txn) for c in spec.conditions)
