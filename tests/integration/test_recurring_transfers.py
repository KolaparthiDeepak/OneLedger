"""Recurring payments list a card bill once, with a readable name (review findings F2 and F7)."""

from __future__ import annotations

from oneledger_domain.recurrence import display_label

from .helpers import account, csv_bytes, import_csv


def _recurring(api) -> list[dict]:  # type: ignore[no-untyped-def]
    api.ok(api.post("/recurring/detect"))
    return api.ok(api.get("/recurring"))


def test_card_bill_is_one_recurring_item_named_after_the_card(api):
    bank = account(api, "HDFC Savings", number="5555666677771234", opening_date="2026-05-31", opening_balance="90000")
    card = account(
        api,
        "HDFC Regalia",
        kind="CREDIT_CARD",
        number="4111111111114321",
        opening_date="2026-05-31",
        opening_balance="0",
    )
    import_csv(
        api,
        bank,
        csv_bytes([f"21/{m:02d}/2026,CC PAYMENT XXXXXXXXXXXX4321 HDFC CARD,CC{m},16000.00,,1.00" for m in (6, 7, 8)]),
    )
    import_csv(
        api,
        card,
        csv_bytes([f"22/{m:02d}/2026,PAYMENT RECEIVED - THANK YOU,CC{m},,16000.00," for m in (6, 7, 8)]),
    )
    items = _recurring(api)
    assert [i["label"] for i in items] == ["Card bill: HDFC Regalia"]
    assert items[0]["account_name"] == "HDFC Savings" and items[0]["typical_amount"].startswith("-16000")


def test_stale_suggestions_are_pruned_but_decisions_are_kept(api):
    bank = account(api, "Bank", opening_date="2026-05-31", opening_balance="90000")
    done = import_csv(
        api, bank, csv_bytes([f"12/{m:02d}/2026,NETFLIX.COM SUBSCRIPTION,N{m},649.00,,1.00" for m in (6, 7, 8)])
    )
    [item] = _recurring(api)
    assert item["label"] == "Netflix"
    api.ok(api.post(f"/imports/{done['id']}/delete", json={"confirm": True}))
    assert _recurring(api) == []  # the evidence is gone, so is the suggestion

    done = import_csv(
        api, bank, csv_bytes([f"12/{m:02d}/2026,NETFLIX.COM SUBSCRIPTION,N{m},649.00,,1.00" for m in (6, 7, 8)])
    )
    [item] = _recurring(api)
    api.ok(api.post(f"/recurring/{item['id']}", json={"state": "ACCEPTED", "label": "Netflix family"}))
    api.ok(api.post(f"/imports/{done['id']}/delete", json={"confirm": True}))
    [kept] = _recurring(api)
    assert kept["state"] == "ACCEPTED" and kept["label"] == "Netflix family"


def test_display_label_keeps_acronyms_and_expands_abbreviations():
    assert display_label("ATM CASH WDL") == "ATM Cash Withdrawal"
    assert display_label("SBI HOME LOAN") == "SBI Home Loan"
    assert display_label("BESCOM ELECTRICITY BILL") == "BESCOM Electricity Bill"
    assert display_label("ZERODHA COIN SIP") == "Zerodha Coin SIP"
