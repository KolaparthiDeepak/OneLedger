"""Known bank statement layouts, matched on the exact column headers.

A preset only pre-fills the column mapping; the user still sees the full preview and confirms.
Add a preset only from a real export's headers -- never from a guessed layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


@dataclass(frozen=True)
class BankPreset:
    key: str
    label: str
    headers: tuple[str, ...]
    mapping: dict[str, Any]

    def matches(self, headers: list[str]) -> bool:
        return [_norm(h) for h in headers if h.strip()][: len(self.headers)] == [_norm(h) for h in self.headers]


PRESETS: tuple[BankPreset, ...] = (
    # HDFC Bank net banking: Accounts -> Account Statement -> "Delimited" (.txt/.csv) or "XLS".
    BankPreset(
        key="hdfc-netbanking",
        label="HDFC Bank net-banking statement",
        headers=("Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"),
        mapping={
            "date_column": "Date",
            "date_format": "DD/MM/YY",
            "value_date_column": "Value Dt",
            "description_columns": ["Narration"],
            "amount_mode": "split",
            "debit_column": "Withdrawal Amt.",
            "credit_column": "Deposit Amt.",
            "reference_column": "Chq./Ref.No.",
            "balance_column": "Closing Balance",
        },
    ),
    # HDFC "Delimited" export variant with long column names.
    BankPreset(
        key="hdfc-delimited",
        label="HDFC Bank delimited statement",
        headers=(
            "Date",
            "Narration",
            "Value Dat",
            "Debit Amount",
            "Credit Amount",
            "Chq/Ref Number",
            "Closing Balance",
        ),
        mapping={
            "date_column": "Date",
            "date_format": "DD/MM/YY",
            "value_date_column": "Value Dat",
            "description_columns": ["Narration"],
            "amount_mode": "split",
            "debit_column": "Debit Amount",
            "credit_column": "Credit Amount",
            "reference_column": "Chq/Ref Number",
            "balance_column": "Closing Balance",
        },
    ),
)


def match_preset(headers: list[str]) -> BankPreset | None:
    return next((p for p in PRESETS if p.matches(headers)), None)


def preset_mapping(preset: BankPreset, headers: list[str], currency: str) -> dict[str, Any]:
    """Rewrite preset column names to the file's exact header spelling (padding/case differ)."""
    by_norm = {_norm(h): h for h in headers}
    out: dict[str, Any] = {"currency": currency}
    for k, v in preset.mapping.items():
        if isinstance(v, list):
            out[k] = [by_norm.get(_norm(x), x) for x in v]
        elif k.endswith("_column"):
            out[k] = by_norm.get(_norm(v), v)
        else:
            out[k] = v
    return out
