"""Deleting an import removes exactly what it added and undoes what depended on it."""

from __future__ import annotations

from decimal import Decimal

from .helpers import account, csv_bytes, import_csv, summary, txns, upload

AUG = ("2026-08-01", "2026-09-01")


def _delete(api, import_id: str) -> dict:
    return api.ok(api.post(f"/imports/{import_id}/delete", json={"confirm": True}))


def test_delete_import_removes_its_transactions_and_allows_reimport(api):
    a = account(api, "HDFC", number="1111222233334444")
    data = csv_bytes(
        [
            "01/08/2026,SALARY ACME,SAL001,,100000.00,100000.00",
            "03/08/2026,UPI/SWIGGY/123456789012,123456789012,650.00,,99350.00",
        ]
    )
    imp = import_csv(api, a, data)
    preview = api.ok(api.get(f"/imports/{imp['id']}/delete-preview"))
    assert preview["transactions_removed"] == 2

    r = api.post(f"/imports/{imp['id']}/delete", json={"confirm": False})
    assert r.status_code == 422
    assert len(txns(api)) == 2

    assert _delete(api, imp["id"])["transactions_removed"] == 2
    assert txns(api) == []
    s = summary(api, *AUG)
    assert Decimal(s["income"]) == 0 and Decimal(s["net_expenses"]) == 0
    assert api.ok(api.get(f"/imports/{imp['id']}"))["state"] == "DELETED"
    # Deleting twice is harmless.
    assert _delete(api, imp["id"])["transactions_removed"] == 0

    # The same file can be imported again and is added fresh (not treated as a replay).
    again = import_csv(api, a, data, name="again.csv")
    assert again["is_replay"] is False and again["counts"]["inserted"] == 2
    assert len(txns(api)) == 2


def test_delete_keeps_transactions_that_existed_before_the_file(api):
    a = account(api, "HDFC")
    first = csv_bytes(["01/08/2026,POS SHOP,,100.00,,900.00"])
    both = csv_bytes(["01/08/2026,POS SHOP,,100.00,,900.00", "02/08/2026,POS OTHER,,50.00,,850.00"])
    import_csv(api, a, first)
    second = import_csv(api, a, both, name="b.csv")
    assert second["counts"]["inserted"] == 1 and second["counts"]["linked"] == 1
    assert api.ok(api.get(f"/imports/{second['id']}/delete-preview"))["shared_transactions_kept"] == 1
    _delete(api, second["id"])
    remaining = txns(api)
    assert len(remaining) == 1 and Decimal(remaining[0]["amount"]) == Decimal("-100")


def test_delete_undoes_transfer_and_restores_other_side(api):
    hdfc = account(api, "HDFC", number="50100011112222")
    union = account(api, "Union Bank", number="33334444")
    import_csv(api, hdfc, csv_bytes(["10/08/2026,NEFT TO SELF UNION,UTR123456789,50000.00,,50000.00"]))
    u = import_csv(api, union, csv_bytes(["10/08/2026,NEFT FROM SELF HDFC,UTR123456789,,50000.00,50000.00"]))
    assert all(t["is_transfer"] for t in txns(api))

    _delete(api, u["id"])
    left = txns(api)
    # The HDFC side is back to how it looks on its own: unmatched, and flagged for review.
    assert len(left) == 1 and left[0]["is_transfer"] is False and left[0]["needs_review"] is True
    assert left[0]["allocations"][0]["transfer_id"] is None
    s = summary(api, *AUG)
    assert Decimal(s["internal_transfers_in"]) == 0


def test_unfinished_import_can_be_discarded(api):
    a = account(api, "HDFC")
    imp = upload(api, a, csv_bytes(["01/08/2026,POS SHOP,,100.00,,900.00"]))
    assert _delete(api, imp["id"])["transactions_removed"] == 0
    assert api.ok(api.get(f"/imports/{imp['id']}"))["state"] == "DELETED"
    assert txns(api) == []


def test_delete_removes_loan_payment_and_restores_outstanding(api):
    bank = account(api, "HDFC", opening_date="2026-07-31", opening_balance="50000.00")
    loan = api.ok(
        api.post(
            "/loans",
            json={
                "lender": "SBI",
                "loan_type": "Home Loan",
                "original_principal": "1000000.00",
                "opening_outstanding": "800000.00",
                "opening_date": "2026-07-31",
                "start_date": "2020-01-05",
                "first_emi_date": "2020-02-05",
                "tenure_months": 240,
                "annual_rate_percent": "8.5",
                "emi_amount": "10000.00",
            },
        ),
        201,
    )
    imp = import_csv(api, bank, csv_bytes(["05/08/2026,NACH SBI HOME LOAN EMI,,10000.00,,40000.00"]))
    emi = next(t for t in txns(api) if t["account"]["id"] == bank)  # auto-matched; lender figures replace it
    api.ok(
        api.post(
            f"/loans/{loan['id']}/payments",
            json={"transaction_id": emi["id"], "principal": "8000.00", "interest": "2000.00"},
        ),
        201,
    )
    _delete(api, imp["id"])
    assert txns(api) == []  # the EMI and the derived loan-account principal movement are both gone
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) == 0 and Decimal(s["loan_principal_paid"]) == 0
    assert Decimal(api.ok(api.get(f"/loans/{loan['id']}"))["outstanding_principal"]) == Decimal("800000")
    bal = {b["name"]: b for b in api.ok(api.get("/analytics/balances"))["data"]["accounts"]}
    assert Decimal(bal["HDFC"]["balance"]) == Decimal("50000")
