"""Suggesting the account a statement file belongs to (polish: detect the account on import)."""

from __future__ import annotations

import uuid
from pathlib import Path

from oneledger_providers.imports import AccountHint, parse_csv, suggest_account

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
HDFC = (FIXTURES / "synthetic_hdfc_jun_aug_2026.csv").read_bytes()
CARD = (FIXTURES / "synthetic_card_jun_aug_2026.csv").read_bytes()


def _acct(name: str, kind: str = "BANK_SAVINGS", last4: str | None = None, **kw) -> AccountHint:  # type: ignore[no-untyped-def]
    return AccountHint(uuid.uuid4(), name, kind, last4, kw.get("institution"), kw.get("previous_headers", ()))


def test_the_account_number_above_the_table_picks_the_account():
    savings, card = _acct("HDFC Savings", last4="1234"), _acct("HDFC Regalia", "CREDIT_CARD", "4321")
    s = suggest_account(parse_csv(HDFC), [card, savings])
    assert s is not None and s.account_id == savings.id and "1234" in s.reason


def test_card_numbers_in_transaction_descriptions_are_ignored():
    # The bank statement's rows mention the card ("CC PAYMENT XXXXXXXXXXXX4321"); only the text
    # above the table counts, so the card must not be suggested for a bank statement.
    card = _acct("HDFC Regalia", "CREDIT_CARD", "4321")
    assert suggest_account(parse_csv(HDFC), [card]) is None


def test_same_columns_as_an_earlier_import():
    table = parse_csv(CARD)
    card = _acct("HDFC Regalia", "CREDIT_CARD", previous_headers=(tuple(table.headers),))
    s = suggest_account(table, [_acct("HDFC Savings"), card])
    assert s is not None and s.account_id == card.id and "same columns" in s.reason


def test_a_known_bank_layout_and_one_account_at_that_bank():
    table = parse_csv(HDFC.split(b"\n", 3)[3])  # drop the preamble with the account number
    hdfc, sbi = _acct("Salary account", institution="HDFC Bank"), _acct("SBI Savings")
    s = suggest_account(table, [sbi, hdfc])
    assert s is not None and s.account_id == hdfc.id and "HDFC" in s.reason


def test_no_suggestion_when_two_accounts_fit():
    table = parse_csv(HDFC.split(b"\n", 3)[3])
    assert suggest_account(table, [_acct("HDFC Savings"), _acct("HDFC Salary")]) is None
