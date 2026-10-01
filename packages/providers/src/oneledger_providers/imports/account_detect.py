"""Suggest which of your accounts a statement file belongs to, so you don't have to pick it.

Three signals, strongest first; a suggestion is made only when exactly one account fits:

1. An account or card number above the transaction table ("Statement of account XXXXXXXX1234")
   whose last four digits match one account. Transaction descriptions are ignored: a bank statement
   mentions card numbers in its card-bill payments.
2. The same column headers as a statement you already imported into one account.
3. A known bank layout (e.g. HDFC net banking) and exactly one bank account at that bank.

It only suggests; you confirm the account before anything is imported.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .presets import match_preset
from .tabular import RawTable

# A run of digits, optionally masked (XXXX, ****) and spaced, ending in four digits.
_NUMBER = re.compile(r"(?<![\dA-Za-z])(?:[Xx*•]{2,}|\d{2,})(?:[\s-]?(?:[Xx*•]{2,}|\d{2,}))*[\s-]?(\d{4})(?!\d)")
BANK_KINDS = ("BANK_SAVINGS", "BANK_CURRENT")


@dataclass(frozen=True, slots=True)
class AccountHint:
    id: uuid.UUID
    name: str
    kind: str
    last4: str | None  # last four digits of the masked account/card number
    institution: str | None
    previous_headers: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class Suggestion:
    account_id: uuid.UUID
    reason: str


def _last4(text: str) -> set[str]:
    return {m.group(1) for m in _NUMBER.finditer(text)}


def suggest_account(table: RawTable, accounts: list[AccountHint]) -> Suggestion | None:
    found = _last4(" \n".join([*table.preamble, *table.headers]))
    if found:
        by_number = [a for a in accounts if a.last4 and a.last4 in found]
        if len(by_number) == 1:
            a = by_number[0]
            return Suggestion(a.id, f"The file mentions the number ending {a.last4}, which is {a.name}.")

    headers = tuple(table.headers)
    by_headers = [a for a in accounts if headers in a.previous_headers]
    if len(by_headers) == 1:
        return Suggestion(
            by_headers[0].id, f"It has the same columns as the last statement you imported into {by_headers[0].name}."
        )

    preset = match_preset(table.headers)
    if preset is not None:
        bank = preset.label.split()[0].lower()  # "HDFC Bank net-banking statement" -> "hdfc"
        at_bank = [a for a in accounts if a.kind in BANK_KINDS and bank in f"{a.name} {a.institution or ''}".lower()]
        if len(at_bank) == 1:
            return Suggestion(
                at_bank[0].id, f"It's a {preset.label}, and {at_bank[0].name} is your only account there."
            )
    return None
