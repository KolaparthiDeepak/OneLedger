"""The import page suggests the account for a statement file; checking a file stores nothing."""

from __future__ import annotations

from pathlib import Path

from .helpers import account, import_csv

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _detect(api, data: bytes) -> dict:  # type: ignore[no-untyped-def]
    r = api.post("/imports/detect-account", files={"file": ("statement.csv", data, "text/csv")})
    return api.ok(r)


def test_detects_the_account_and_keeps_nothing(api):
    savings = account(api, "HDFC Savings", number="50100012341234")
    card = account(api, "HDFC Regalia", kind="CREDIT_CARD", number="4111111111114321")
    hdfc = (FIXTURES / "synthetic_hdfc_jun_aug_2026.csv").read_bytes()
    found = _detect(api, hdfc)
    assert found["account_id"] == savings and "1234" in found["reason"]
    assert api.ok(api.get("/imports")) == []  # nothing uploaded or stored

    statement = (FIXTURES / "synthetic_card_jun_aug_2026.csv").read_bytes()
    assert _detect(api, statement)["account_id"] is None  # no number, no history yet
    import_csv(
        api,
        card,
        statement,
        mapping={
            "date_column": "Date",
            "date_format": "DD/MM/YYYY",
            "description_columns": ["Transaction Details"],
            "amount_mode": "drcr_column",
            "amount_column": "Amount",
            "drcr_column": "Dr/Cr",
        },
    )
    assert _detect(api, statement)["account_id"] == card  # same columns as its last import


def test_an_unreadable_file_gets_no_suggestion(api):
    account(api, "HDFC Savings", number="1234")
    assert _detect(api, b"\x00\x01 not a statement")["account_id"] is None
