"""Shared expenses, settling up, manual transfers, templates and receipt attachments."""

from __future__ import annotations

import io
from decimal import Decimal

from .helpers import account, csv_bytes, import_csv, summary, txns

AUG = ("2026-08-01", "2026-09-01")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _person(api, name: str) -> dict:  # type: ignore[no-untyped-def]
    return api.ok(api.post("/people", json={"name": name}), 201)


def _people(api) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    return {p["name"]: p for p in api.ok(api.get("/people"))}


def test_sharing_a_dinner_counts_only_your_part_as_spending(api):
    bank = account(api, "HDFC", opening_date="2026-07-31", opening_balance="10000.00")
    import_csv(api, bank, csv_bytes(["10/08/2026,UPI/BARBEQUE NATION/1,1,3000.00,,7000.00"]))
    dinner = txns(api)[0]
    priya, arjun = _person(api, "Priya"), _person(api, "Arjun")
    api.ok(
        api.post(
            f"/transactions/{dinner['id']}/share",
            json={
                "shares": [{"person_id": priya["id"], "amount": "1000"}, {"person_id": arjun["id"], "amount": "1000"}]
            },
        )
    )
    assert Decimal(summary(api, *AUG)["net_expenses"]) == 1000
    people = _people(api)
    assert people["Priya"]["owes_you"] == "1000.00" and people["Arjun"]["owes_you"] == "1000.00"
    # Priya pays back by UPI; the credit on the statement is linked, not added twice.
    import_csv(api, bank, csv_bytes(["12/08/2026,UPI/PRIYA/2,2,,1000.00,8000.00"]), name="later.csv")
    credit = next(t for t in txns(api) if t["amount"] == "1000.00" and t["account"]["id"] == bank)
    api.ok(
        api.post(
            f"/people/{priya['id']}/settle",
            json={
                "account_id": bank,
                "amount": "1000",
                "transaction_date": "2026-08-12",
                "transaction_id": credit["id"],
            },
        ),
        201,
    )
    assert _people(api)["Priya"]["balance"] == "0.00"
    s = summary(api, *AUG)
    assert Decimal(s["income"]) == 0 and Decimal(s["net_expenses"]) == 1000  # repayment is not income
    # Net worth includes what Arjun still owes.
    nw = api.ok(api.get("/analytics/net-worth"))["data"]["totals"]["INR"]
    assert Decimal(nw["assets"]) == Decimal("8000") + Decimal("1000")


def test_they_paid_for_you_then_you_pay_them_back_in_cash(api):
    cash = account(api, "Cash", kind="CASH", opening_date="2026-07-31", opening_balance="2000.00")
    rahul = _person(api, "Rahul")
    food = next(c for c in api.ok(api.get("/categories")) if c["code"] == "FOOD")
    api.ok(
        api.post(
            f"/people/{rahul['id']}/they-paid",
            json={
                "amount": "450",
                "transaction_date": "2026-08-03",
                "description": "Movie snacks",
                "category_id": food["id"],
            },
        ),
        201,
    )
    assert _people(api)["Rahul"]["you_owe"] == "450.00"
    assert Decimal(summary(api, *AUG)["net_expenses"]) == 450
    api.ok(
        api.post(
            f"/people/{rahul['id']}/settle",
            json={"account_id": cash, "amount": "-450", "transaction_date": "2026-08-04"},
        ),
        201,
    )
    assert _people(api)["Rahul"]["balance"] == "0.00"
    assert Decimal(summary(api, *AUG)["net_expenses"]) == 450  # paying back is not spending again


def test_unsharing_restores_the_original_expense(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["10/08/2026,UPI/SWIGGY/1,1,900.00,,1.00"]))
    t = txns(api)[0]
    p = _person(api, "Meera")
    api.ok(api.post(f"/transactions/{t['id']}/share", json={"shares": [{"person_id": p["id"], "amount": "300"}]}))
    api.ok(api.delete(f"/transactions/{t['id']}/share"))
    assert Decimal(summary(api, *AUG)["net_expenses"]) == 900
    assert _people(api)["Meera"]["balance"] == "0.00"


def test_shares_cannot_exceed_the_payment(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["10/08/2026,UPI/SWIGGY/1,1,900.00,,1.00"]))
    t = txns(api)[0]
    p = _person(api, "Meera")
    r = api.post(f"/transactions/{t['id']}/share", json={"shares": [{"person_id": p["id"], "amount": "901"}]})
    assert r.status_code == 422


def test_manual_transfer_is_never_income_or_spending(api):
    bank = account(api, "HDFC", opening_date="2026-07-31", opening_balance="5000.00")
    cash = account(api, "Cash", kind="CASH", opening_date="2026-07-31", opening_balance="0.00")
    r = api.ok(
        api.post(
            "/transfers/manual",
            json={"from_account_id": bank, "to_account_id": cash, "amount": "1500", "transaction_date": "2026-08-02"},
            headers={"Idempotency-Key": "k1"},
        ),
        201,
    )
    again = api.ok(
        api.post(
            "/transfers/manual",
            json={"from_account_id": bank, "to_account_id": cash, "amount": "1500", "transaction_date": "2026-08-02"},
            headers={"Idempotency-Key": "k1"},
        ),
        201,
    )
    assert r == again  # a double tap does not move the money twice
    s = summary(api, *AUG)
    assert Decimal(s["income"]) == 0 and Decimal(s["net_expenses"]) == 0
    bal = {b["name"]: b["balance"] for b in api.ok(api.get("/analytics/balances"))["data"]["accounts"]}
    assert Decimal(bal["HDFC"]) == 3500 and Decimal(bal["Cash"]) == 1500
    bad = api.post(
        "/transfers/manual",
        json={"from_account_id": bank, "to_account_id": bank, "amount": "1", "transaction_date": "2026-08-02"},
    )
    assert bad.status_code == 422


def test_templates_round_trip_and_sort_by_use(api):
    bank = account(api, "HDFC")
    cash = account(api, "Cash", kind="CASH")
    milk = api.ok(
        api.post("/templates", json={"name": "Milk", "kind": "expense", "account_id": cash, "amount": "60"}), 201
    )
    api.ok(
        api.post("/templates", json={"name": "ATM", "kind": "transfer", "account_id": bank, "to_account_id": cash}), 201
    )
    api.ok(api.post(f"/templates/{milk['id']}/used"))
    names = [t["name"] for t in api.ok(api.get("/templates"))]
    assert names == ["Milk", "ATM"]
    bad = api.post("/templates", json={"name": "x", "kind": "transfer", "account_id": bank})
    assert bad.status_code == 422
    assert api.delete(f"/templates/{milk['id']}").status_code == 204


def test_receipts_are_stored_encrypted_and_type_checked(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["10/08/2026,DMART,1,900.00,,1.00"]))
    t = txns(api)[0]
    up = api.ok(
        api.post(f"/transactions/{t['id']}/attachments", files={"file": ("bill.png", io.BytesIO(PNG), "image/png")}),
        201,
    )
    assert up["content_type"] == "image/png" and up["size_bytes"] == len(PNG)
    got = api.get(f"/attachments/{up['id']}")
    assert got.status_code == 200 and got.content == PNG and got.headers["x-content-type-options"] == "nosniff"
    fake = api.post(
        f"/transactions/{t['id']}/attachments", files={"file": ("x.png", io.BytesIO(b"<script>"), "image/png")}
    )
    assert fake.status_code == 422
    assert [x["id"] for x in api.ok(api.get(f"/transactions/{t['id']}/attachments"))] == [up["id"]]
    assert api.delete(f"/attachments/{up['id']}").status_code == 204
    assert api.ok(api.get(f"/transactions/{t['id']}/attachments")) == []


def test_another_ledger_cannot_reach_your_people_templates_or_receipts(api, ctx, client):
    from ..conftest import login, make_owner

    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["10/08/2026,DMART,1,900.00,,1.00"]))
    t = txns(api)[0]
    person = _person(api, "Priya")
    tpl = api.ok(api.post("/templates", json={"name": "Milk", "kind": "expense", "account_id": bank}), 201)
    att = api.ok(
        api.post(f"/transactions/{t['id']}/attachments", files={"file": ("b.png", io.BytesIO(PNG), "image/png")}), 201
    )
    make_owner(ctx, "other@example.com")
    other = login(client, "other@example.com")
    assert other.get(f"/people/{person['id']}").status_code == 404
    assert other.get("/people").json() == []
    assert other.get("/templates").json() == []
    assert other.post(f"/templates/{tpl['id']}/used").status_code == 404
    assert other.get(f"/attachments/{att['id']}").status_code == 404
    assert other.delete(f"/attachments/{att['id']}").status_code == 404
    assert (
        other.post(
            f"/transactions/{t['id']}/share", json={"shares": [{"person_id": person["id"], "amount": "1"}]}
        ).status_code
        == 404
    )
