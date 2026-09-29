"""Net worth history starts from recorded balances, not from the first daily snapshot (finding F4)."""

from __future__ import annotations

from decimal import Decimal

from .helpers import account, csv_bytes, import_csv


def test_history_is_rebuilt_from_statement_balances(api):
    bank = account(api, "HDFC", opening_date="2026-05-31", opening_balance="45000.00")
    import_csv(
        api,
        bank,
        csv_bytes(
            [
                "01/06/2026,SALARY ACME,S6,,100000.00,145000.00",
                "10/07/2026,RENT,R7,30000.00,,115000.00",
                "01/08/2026,SALARY ACME,S8,,100000.00,215000.00",
            ]
        ),
    )
    points = api.ok(api.get("/analytics/net-worth/history", params={"days": 365}))["points"]
    by_date = {p["date"]: p for p in points if p["source"] == "computed"}
    assert Decimal(by_date["2026-05-31"]["net_worth"]) == Decimal("45000")
    assert Decimal(by_date["2026-06-30"]["net_worth"]) == Decimal("145000")
    assert Decimal(by_date["2026-07-31"]["net_worth"]) == Decimal("115000")
    assert Decimal(by_date["2026-08-31"]["net_worth"]) == Decimal("215000")
    assert not any(p["partial"] for p in by_date.values())


def test_months_with_an_unknown_balance_are_marked_partial(api):
    account(api, "HDFC", opening_date="2026-06-30", opening_balance="1000.00")
    account(api, "SBI", opening_date="2026-08-15", opening_balance="500.00")
    points = api.ok(api.get("/analytics/net-worth/history"))["points"]
    by_date = {p["date"]: p for p in points}
    # SBI has no balance before 15 Aug, but it was also not open yet: July is complete.
    assert by_date["2026-07-31"]["partial"] is False and Decimal(by_date["2026-07-31"]["net_worth"]) == 1000
    assert Decimal(by_date["2026-08-31"]["net_worth"]) == 1500


def test_empty_ledger_has_no_history(api):
    assert api.ok(api.get("/analytics/net-worth/history"))["points"] == []


def test_account_balance_history_follows_statement_balances(api):
    bank = account(api, "HDFC", opening_date="2026-07-31", opening_balance="1000.00")
    import_csv(
        api,
        bank,
        csv_bytes(["02/08/2026,SALARY ACME,S1,,5000.00,6000.00", "04/08/2026,DMART,D1,500.00,,5500.00"]),
    )
    pts = api.ok(api.get(f"/accounts/{bank}/balance-history", params={"days": 400}))["points"]
    by = {p["date"]: Decimal(p["balance"]) for p in pts}
    assert by["2026-07-31"] == 1000 and by["2026-08-02"] == 6000 and by["2026-08-04"] == 5500
    assert Decimal(pts[-1]["balance"]) == 5500
