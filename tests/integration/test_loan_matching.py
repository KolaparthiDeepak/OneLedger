"""EMI debits are matched to loans so only interest counts as spending (review finding F1)."""

from __future__ import annotations

from decimal import Decimal

from .helpers import account, csv_bytes, import_csv, summary, txns

AUG = ("2026-08-01", "2026-09-01")
SEP = ("2026-09-01", "2026-10-01")
LOAN = {
    "lender": "SBI",
    "loan_type": "Home Loan",
    "original_principal": "1000000.00",
    "opening_outstanding": "800000.00",
    "opening_date": "2026-07-31",
    "start_date": "2020-01-05",
    "first_emi_date": "2020-02-05",
    "tenure_months": 240,
    "annual_rate_percent": "12",
    "emi_amount": "10000.00",
}
# 12% a year on 8,00,000 is 8,000 interest for the first month; principal 2,000.
EMIS = [
    "05/08/2026,NACH DR SBI HOME LOAN EMI,N1,10000.00,,40000.00",
    "05/09/2026,NACH DR SBI HOME LOAN EMI,N2,10000.00,,30000.00",
]


def _loan(api, **over):  # type: ignore[no-untyped-def]
    return api.ok(api.post("/loans", json={**LOAN, **over}), 201)


def _detail(api, loan_id: str) -> dict:
    return api.ok(api.get(f"/loans/{loan_id}"))


def _bank(api) -> str:  # type: ignore[no-untyped-def]
    return account(api, "HDFC", opening_date="2026-07-31", opening_balance="50000.00")


def test_emi_imported_after_loan_is_split_automatically(api):
    bank = _bank(api)
    loan = _loan(api)
    done = import_csv(api, bank, csv_bytes(EMIS))
    assert done["counts"]["loan_payments_recorded"] == 2
    d = _detail(api, loan["id"])
    assert d["payments_recorded"] == 2
    assert all(not p["actual"] and p["source"] == "AUTO_ESTIMATE" for p in d["payments"])
    aug = summary(api, *AUG)
    assert Decimal(aug["net_expenses"]) == Decimal("8000.00")
    assert Decimal(aug["loan_principal_paid"]) == Decimal("2000.00")
    # Second month's interest uses the reduced balance (7,98,000 at 1% a month).
    sep = summary(api, *SEP)
    assert Decimal(sep["net_expenses"]) == Decimal("7980.00")
    assert Decimal(d["outstanding_principal"]) == Decimal("800000") - Decimal("2000") - Decimal("2020")
    emi = next(t for t in txns(api) if t["transaction_date"] == "2026-08-05" and t["account"]["name"] == "HDFC")
    # Split by OneLedger, not by the owner: interest is SYSTEM, principal is the matched transfer leg.
    assert emi["is_split"] and {a["classification_source"] for a in emi["allocations"]} == {"SYSTEM", "TRANSFER_MATCH"}


def test_statement_imported_before_loan_is_matched_when_loan_is_added(api):
    bank = _bank(api)
    import_csv(api, bank, csv_bytes(EMIS))
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("10000")
    loan = _loan(api)
    assert loan["matched"] == {"recorded": 2, "suggested": 0}
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("8000.00")


def test_emi_without_loan_words_waits_in_review_and_dismissal_is_remembered(api):
    bank = _bank(api)
    loan = _loan(api)
    import_csv(api, bank, csv_bytes(["05/08/2026,ACH D- ABC 998877,A1,10000.00,,40000.00"]))
    assert _detail(api, loan["id"])["payments_recorded"] == 0
    items = api.ok(api.get("/review", params={"kind": "LOAN_PAYMENT_SUGGESTION"}))
    assert len(items) == 1 and items[0]["related"]["loan_id"] == loan["id"]
    api.ok(api.post(f"/review/{items[0]['id']}/dismiss"))
    assert api.ok(api.post(f"/loans/{loan['id']}/match-payments")) == {"recorded": 0, "suggested": 0}
    assert api.ok(api.get("/review", params={"kind": "LOAN_PAYMENT_SUGGESTION"})) == []


def test_recording_a_suggested_emi_resolves_the_review_item(api):
    bank = _bank(api)
    loan = _loan(api)
    import_csv(api, bank, csv_bytes(["05/08/2026,ACH D- ABC 998877,A1,10000.00,,40000.00"]))
    item = api.ok(api.get("/review", params={"kind": "LOAN_PAYMENT_SUGGESTION"}))[0]
    api.ok(api.post(f"/loans/{loan['id']}/payments", json={"transaction_id": item["transactions"][0]["id"]}), 201)
    assert api.ok(api.get("/review", params={"kind": "LOAN_PAYMENT_SUGGESTION"})) == []


def test_lender_figures_replace_an_automatic_estimate(api):
    bank = _bank(api)
    loan = _loan(api)
    import_csv(api, bank, csv_bytes(EMIS[:1]))
    emi = next(t for t in txns(api) if t["account"]["name"] == "HDFC")
    p = api.ok(
        api.post(
            f"/loans/{loan['id']}/payments",
            json={"transaction_id": emi["id"], "principal": "2500.00", "interest": "7500.00"},
        ),
        201,
    )
    assert p["actual"] is True
    d = _detail(api, loan["id"])
    assert d["payments_recorded"] == 1
    assert Decimal(d["outstanding_principal"]) == Decimal("797500")
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("7500.00")
    # Lender figures are final: a second attempt conflicts.
    r = api.post(f"/loans/{loan['id']}/payments", json={"transaction_id": emi["id"]})
    assert r.status_code == 409


def test_removing_a_payment_restores_the_debit(api):
    bank = _bank(api)
    loan = _loan(api)
    import_csv(api, bank, csv_bytes(EMIS[:1]))
    pay = _detail(api, loan["id"])["payments"][0]
    api.ok(api.delete(f"/loans/{loan['id']}/payments/{pay['id']}"))
    d = _detail(api, loan["id"])
    assert d["payments_recorded"] == 0
    assert Decimal(d["outstanding_principal"]) == Decimal("800000")
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("10000")
    emi = next(t for t in txns(api) if t["account"]["name"] == "HDFC")
    assert not emi["is_split"] and not emi["is_transfer"]
    # The derived loan-account movement is gone too.
    assert [t for t in txns(api) if t["account"]["kind"] == "LOAN"] == []


def test_debits_before_the_opening_balance_or_set_by_the_owner_are_left_alone(api):
    bank = account(api, "HDFC", opening_date="2026-06-30", opening_balance="60000.00")
    import_csv(
        api,
        bank,
        csv_bytes(
            [
                "05/07/2026,NACH DR SBI HOME LOAN EMI,N0,10000.00,,50000.00",
                "05/08/2026,NACH DR SBI HOME LOAN EMI,N1,10000.00,,40000.00",
            ]
        ),
    )
    aug = next(t for t in txns(api) if t["transaction_date"] == "2026-08-05")
    rent = next(c for c in api.ok(api.get("/categories")) if c["code"] == "HOUSING_RENT")
    api.ok(api.post(f"/transactions/{aug['id']}/classify", json={"category_id": rent["id"], "effect": "expense"}))
    loan = _loan(api)
    assert loan["matched"] == {"recorded": 0, "suggested": 0}
    assert _detail(api, loan["id"])["payments_recorded"] == 0


def test_deleting_the_import_removes_automatic_payments(api):
    bank = _bank(api)
    loan = _loan(api)
    done = import_csv(api, bank, csv_bytes(EMIS))
    api.ok(api.post(f"/imports/{done['id']}/delete", json={"confirm": True}))
    d = _detail(api, loan["id"])
    assert d["payments_recorded"] == 0
    assert Decimal(d["outstanding_principal"]) == Decimal("800000")
