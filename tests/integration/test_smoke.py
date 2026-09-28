from __future__ import annotations


def test_login_and_accounts(api):
    me = api.ok(api.get("/me"))
    assert me["base_currency"] == "INR"
    acct = api.ok(
        api.post(
            "/accounts",
            json={
                "name": "HDFC Savings",
                "kind": "BANK_SAVINGS",
                "account_number": "50100012345678",
                "opening_date": "2026-07-31",
                "opening_balance": "10000.00",
            },
        ),
        201,
    )
    assert acct["masked_identifier"] == "XXXXXXXX5678"
    listed = api.ok(api.get("/accounts"))
    assert listed[0]["balance"] == "10000.00000000" or listed[0]["balance"].startswith("10000")
