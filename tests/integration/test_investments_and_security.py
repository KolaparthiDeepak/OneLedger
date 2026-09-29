"""SIP -> holding, AMFI prices, XIRR, goals editing, export, password and two-step sign-in."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pyotp
from oneledger_domain.xirr import xirr

from .helpers import account, csv_bytes, import_csv, summary

AMFI = """Open Ended Schemes(Equity Scheme - Large Cap Fund)

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
120465;INF846K01EW2;-;Axis Bluechip Fund - Direct Plan - Growth;62.4500;26-Sep-2026
118834;INF204K01XI3;-;Nippon India Nifty 50 Index Fund - Direct - Growth;38.1200;26-Sep-2026
"""


def test_xirr_known_answer():
    # 1,00,000 in, 1,10,000 back exactly one year later -> 10%.
    assert xirr([(date(2025, 1, 1), Decimal("-100000")), (date(2026, 1, 1), Decimal("110000"))]) == Decimal("0.1")
    assert xirr([(date(2025, 1, 1), Decimal("-100"))]) is None


def test_detected_sip_becomes_a_holding_with_its_contributions(api):
    bank = account(api, "HDFC")
    import_csv(
        api, bank, csv_bytes([f"07/{m:02d}/2026,ACH D- ZERODHA COIN SIP,Z{m},10000.00,,1.00" for m in (6, 7, 8)])
    )
    api.ok(api.post("/recurring/detect"))
    [sip] = api.ok(api.get("/recurring"))
    h = api.ok(api.post("/investments/from-recurring", json={"recurring_id": sip["id"], "name": "Axis Bluechip"}), 201)
    assert h["contributions"] == "30000.00" and h["funded_by_recurring"]
    s = summary(api, "2026-06-01", "2026-09-01")
    assert Decimal(s["net_expenses"]) == 0 and Decimal(s["investment_contributions"]) == Decimal("30000")
    again = api.post("/investments/from-recurring", json={"recurring_id": sip["id"]})
    assert again.status_code == 409


def test_amfi_price_values_units_and_gives_xirr(api, monkeypatch):
    from oneledger_api.services import market

    monkeypatch.setattr(market, "_cache", None)
    monkeypatch.setattr(market, "_download", lambda: AMFI)
    assert [r["scheme_code"] for r in api.ok(api.get("/investments/fund-search", params={"q": "axis bluechip"}))] == [
        "120465"
    ]
    inv = api.ok(
        api.post(
            "/investments",
            json={
                "name": "Axis Bluechip",
                "instrument_type": "MUTUAL_FUND",
                "identifier": "120465",
                "valuation_mode": "UNITS",
            },
        ),
        201,
    )
    api.ok(
        api.post(
            f"/investments/{inv['id']}/transactions",
            json={
                "action": "BUY",
                "trade_date": "2025-09-26",
                "units": "1000",
                "unit_price": "50",
                "gross_amount": "50000",
            },
        ),
        201,
    )
    h = api.ok(api.post(f"/investments/{inv['id']}/refresh-price"), 201)
    assert h["value"] == "62450.00" and h["valuation_source"] == "AMFI" and h["valued_on"] == "2026-09-26"
    assert Decimal(h["xirr_percent"]) == Decimal("24.90")
    bad = api.ok(api.post("/investments", json={"name": "Gold", "instrument_type": "GOLD"}), 201)
    assert api.post(f"/investments/{bad['id']}/refresh-price").status_code == 422


def test_goal_can_be_edited_and_shows_contributions(api):
    g = api.ok(api.post("/goals", json={"name": "Trip", "target_amount": "50000"}), 201)
    api.ok(api.post(f"/goals/{g['id']}/contributions", json={"amount": "5000", "contribution_date": "2026-08-01"}), 201)
    g2 = api.ok(
        api.patch(f"/goals/{g['id']}", json={"version": g["version"], "name": "Goa trip", "target_amount": "60000"})
    )
    assert g2["name"] == "Goa trip" and g2["target_amount"] == "60000.00" and len(g2["contributions"]) == 1
    stale = api.patch(f"/goals/{g['id']}", json={"version": g["version"], "name": "x"})
    assert stale.status_code == 409


def test_json_export_has_the_whole_ledger(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["03/08/2026,DMART,1,1000.00,,1.00"]))
    r = api.get("/export/ledger.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["format"] == "oneledger-export-v1"
    assert [t["amount"] for t in data["transactions"]] == ["-1000.00"]
    assert data["transactions"][0]["allocations"][0]["effect"] == "expense"
    assert all("owner_id" not in a for a in data["accounts"])


def test_change_password_signs_out_other_devices(api, client):
    from ..conftest import OWNER_PASSWORD, login

    other = login(client)
    bad = api.post("/auth/password", json={"current_password": "wrong", "new_password": "a brand new passphrase"})
    assert bad.status_code == 401
    r = api.ok(
        api.post("/auth/password", json={"current_password": OWNER_PASSWORD, "new_password": "a brand new passphrase"})
    )
    assert r["signed_out"] >= 1
    assert other.get("/me").status_code == 401
    assert api.get("/me").status_code == 200
    short = api.post("/auth/password", json={"current_password": "a brand new passphrase", "new_password": "short"})
    assert short.status_code == 422


def test_two_step_sign_in_can_be_turned_on_and_off(api):
    from ..conftest import OWNER_PASSWORD

    secret = api.ok(api.post("/auth/mfa/enroll"))["secret"]
    api.ok(api.post("/auth/mfa/enroll/confirm", json={"code": pyotp.TOTP(secret).now()}))
    assert api.ok(api.get("/me"))["mfa_enabled"] is True
    wrong = api.post("/auth/mfa/disable", json={"code": "000000", "password": OWNER_PASSWORD})
    assert wrong.status_code == 401
    api.ok(api.post("/auth/mfa/disable", json={"code": pyotp.TOTP(secret).now(), "password": OWNER_PASSWORD}))
    assert api.ok(api.get("/me"))["mfa_enabled"] is False
