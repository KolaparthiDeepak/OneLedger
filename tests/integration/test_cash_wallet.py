"""ATM withdrawals move into the cash wallet instead of counting as spending (review finding F3)."""

from __future__ import annotations

from decimal import Decimal

from .helpers import account, csv_bytes, import_csv, summary, txns

AUG = ("2026-08-01", "2026-09-01")
ATM = ["04/08/2026,ATM CASH WDL MG ROAD,,4000.00,,1.00", "25/08/2026,NWD CASH WITHDRAWAL,,2000.00,,1.00"]


def _wallet(api, **kw):  # type: ignore[no-untyped-def]
    body = {"name": "Cash", "kind": "CASH", "opening_date": "2026-07-31", "opening_balance": "500.00", **kw}
    return api.ok(api.post("/accounts", json=body), 201)


def _balance(api, name: str) -> Decimal:  # type: ignore[no-untyped-def]
    bal = {b["name"]: b for b in api.ok(api.get("/analytics/balances"))["data"]["accounts"]}
    return Decimal(bal[name]["balance"])


def test_without_a_wallet_withdrawals_stay_cash_spending(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(ATM))
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("6000")


def test_adding_a_wallet_moves_past_withdrawals_into_it(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(ATM))
    wallet = _wallet(api)
    assert wallet["cash_withdrawals_linked"] == 2
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("0")
    assert _balance(api, "Cash") == Decimal("6500")
    moved = [t for t in txns(api) if t["account"]["name"] == "Cash"]
    assert {t["description"] for t in moved} == {"Cash from ATM"}
    assert all(t["allocations"][0]["category"]["code"] == "TRANSFERS_CASH" for t in moved)


def test_withdrawals_imported_after_the_wallet_link_automatically(api):
    _wallet(api)
    bank = account(api, "HDFC")
    done = import_csv(api, bank, csv_bytes(ATM))
    assert done["counts"]["cash_withdrawals_linked"] == 2
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("0")


def test_an_existing_wallet_entry_is_paired_not_duplicated(api):
    w = _wallet(api)
    api.ok(
        api.post(
            "/transactions",
            json={"account_id": w["id"], "amount": "4000.00", "transaction_date": "2026-08-05", "description": "ATM"},
        ),
        201,
    )
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(ATM[:1]))
    assert len([t for t in txns(api) if t["account"]["name"] == "Cash"]) == 1
    assert _balance(api, "Cash") == Decimal("4500")


def test_owner_choice_and_opening_balance_are_respected(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["30/07/2026,ATM CASH WDL,,1000.00,,1.00", *ATM]))
    atm = next(t for t in txns(api) if t["transaction_date"] == "2026-08-25")
    travel = next(c for c in api.ok(api.get("/categories")) if c["code"] == "TRAVEL")
    api.ok(api.post(f"/transactions/{atm['id']}/classify", json={"category_id": travel["id"], "effect": "expense"}))
    wallet = _wallet(api)
    # 30 Jul is before the wallet's opening balance; 25 Aug was marked as travel by the owner.
    assert wallet["cash_withdrawals_linked"] == 1


def test_deleting_the_statement_removes_the_wallet_movement(api):
    _wallet(api)
    bank = account(api, "HDFC")
    done = import_csv(api, bank, csv_bytes(ATM))
    api.ok(api.post(f"/imports/{done['id']}/delete", json={"confirm": True}))
    assert [t for t in txns(api) if t["account"]["name"] == "Cash"] == []
    assert _balance(api, "Cash") == Decimal("500")
