"""Calendar totals, upcoming bills, safe to spend, alerts, cash-flow diagram, budgets (roadmap features)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from oneledger_domain.periods import add_months

from .helpers import account, csv_bytes, import_csv

TODAY = date.today()


def _d(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _month_start(offset: int) -> date:
    return add_months(TODAY.replace(day=1), offset)


def test_daily_totals_match_the_summary(api):
    bank = account(api, "HDFC")
    import_csv(
        api,
        bank,
        csv_bytes(
            [
                "01/08/2026,SALARY ACME,S1,,100000.00,1.00",
                "03/08/2026,UPI/SWIGGY/1,1,650.00,,1.00",
                "03/08/2026,DMART,2,1350.00,,1.00",
            ]
        ),
    )
    out = api.ok(api.get("/analytics/daily", params={"start_date": "2026-08-01", "end_date_exclusive": "2026-09-01"}))
    days = {d["date"]: d for d in out["days"]}
    assert Decimal(days["2026-08-01"]["money_in"]) == 100000 and Decimal(days["2026-08-01"]["spent"]) == 0
    assert Decimal(days["2026-08-03"]["spent"]) == 2000 and days["2026-08-03"]["transaction_count"] == 2


def test_upcoming_bills_include_loan_emi_and_card_statement(api):
    bank = account(api, "HDFC", opening_date=_iso(TODAY - timedelta(days=1)), opening_balance="50000.00")
    due = TODAY + timedelta(days=4)
    api.ok(
        api.post(
            "/loans",
            json={
                "lender": "SBI",
                "loan_type": "Home Loan",
                "original_principal": "1000000.00",
                "opening_outstanding": "800000.00",
                "opening_date": _iso(TODAY - timedelta(days=1)),
                "start_date": "2020-01-01",
                "first_emi_date": _iso(add_months(due, -60)),
                "tenure_months": 240,
                "annual_rate_percent": "8.5",
                "emi_amount": "10000.00",
            },
        ),
        201,
    )
    card = api.ok(
        api.post(
            "/cards",
            json={
                "name": "Regalia",
                "issuer": "HDFC",
                "credit_limit": "300000",
                "outstanding": "12000",
                "outstanding_as_of": _iso(TODAY - timedelta(days=20)),
            },
        ),
        201,
    )
    api.ok(
        api.post(
            f"/cards/{card['id']}/statements",
            json={
                "period_start": _iso(TODAY - timedelta(days=40)),
                "period_end": _iso(TODAY - timedelta(days=10)),
                "statement_balance": "12000",
                "minimum_due": "600",
                "due_date": _iso(TODAY + timedelta(days=2)),
            },
        ),
        201,
    )
    bills = api.ok(api.get("/insights/upcoming", params={"days": 10}))
    kinds = {(b["kind"], b["amount"]) for b in bills}
    assert ("loan", "10000.00") in kinds and ("card", "12000.00") in kinds
    safe = api.ok(api.get("/insights/safe-to-spend"))
    assert safe["available"] and Decimal(safe["liquid_balance"]) == 50000
    assert Decimal(safe["obligations_total"]) >= Decimal("22000")
    assert any("No regular income" in n for n in safe["assumptions"])
    # Both bills are within three days -> alerts; a dismissal sticks.
    alerts = api.ok(api.get("/insights/alerts"))
    card_alert = next(a for a in alerts if a["key"].startswith("bill:card"))
    api.ok(api.post("/insights/alerts/dismiss", json={"key": card_alert["key"]}))
    assert card_alert["key"] not in {a["key"] for a in api.ok(api.get("/insights/alerts"))}
    del bank


def _iso(d: date) -> str:
    return d.isoformat()


def test_budget_over_alert_and_rollover(api):
    bank = account(api, "HDFC")
    last = _month_start(-1)
    this = _month_start(0)
    import_csv(
        api,
        bank,
        csv_bytes(
            [
                f"{_d(last + timedelta(days=2))},DMART GROCERIES,1,3000.00,,1.00",
                f"{_d(this)},DMART GROCERIES,2,6500.00,,1.00",
            ]
        ),
    )
    food = next(c for c in api.ok(api.get("/categories")) if c["code"] == "FOOD")
    api.ok(
        api.post(
            "/budgets",
            json={
                "name": "Food",
                "category_id": food["id"],
                "amount": "5000",
                "start_date": _iso(last),
                "rollover": True,
            },
        ),
        201,
    )
    [b] = api.ok(api.get("/budgets"))["data"]["budgets"]
    # 2,000 left last month carries over: 7,000 available, 6,500 spent -> not over.
    assert Decimal(b["carried_over"]) == 2000 and Decimal(b["available"]) == 7000 and b["over"] is False
    api.ok(api.patch(f"/budgets/{b['id']}", json={"version": b["version"], "rollover": False}))
    [b] = api.ok(api.get("/budgets"))["data"]["budgets"]
    assert b["over"] is True
    assert any(a["key"].startswith("budget_over:") for a in api.ok(api.get("/insights/alerts")))


def test_sankey_balances_money_in_and_out(api):
    bank = account(api, "HDFC")
    import_csv(
        api,
        bank,
        csv_bytes(
            [
                "01/08/2026,SALARY ACME,S1,,100000.00,1.00",
                "05/08/2026,NOBROKER RENT PAYMENT,R1,30000.00,,1.00",
                "06/08/2026,DMART,D1,5000.00,,1.00",
            ]
        ),
    )
    s = api.ok(api.get("/analytics/sankey", params={"start_date": "2026-08-01", "end_date_exclusive": "2026-09-01"}))
    names = [n["name"] for n in s["nodes"]]
    hub = names.index("Money in")
    inflow = sum(Decimal(link["value"]) for link in s["links"] if link["target"] == hub)
    outflow = sum(Decimal(link["value"]) for link in s["links"] if link["source"] == hub)
    assert inflow == outflow == Decimal("100000")
    kept = next(link for link in s["links"] if s["nodes"][link["target"]]["name"] == "Left over")
    assert Decimal(kept["value"]) == 65000


def test_budget_suggestions_average_three_full_months(api):
    bank = account(api, "HDFC")
    rows = [
        f"{_d(_month_start(-i) + timedelta(days=3))},DMART GROCERIES,{i},{amt},,1.00"
        for i, amt in ((1, "4200.00"), (2, "3900.00"), (3, "4500.00"))
    ]
    import_csv(api, bank, csv_bytes(rows))
    [s] = api.ok(api.get("/budgets/suggestions"))
    assert s["name"] == "Food" and Decimal(s["average"]) == 4200 and s["suggested"] == "4500"


def test_dashboard_can_show_any_month(api):
    bank = account(api, "HDFC")
    import_csv(api, bank, csv_bytes(["03/07/2026,DMART,1,1000.00,,1.00", "03/08/2026,DMART,2,2000.00,,1.00"]))
    d = api.ok(api.get("/analytics/dashboard", params={"month": "2026-07"}))
    assert d["period"]["start"] == "2026-07-01" and d["period"]["note"] is None
    assert Decimal(d["summary"]["data"]["net_expenses"]) == 1000
    assert d["data_range"] == {"first": "2026-07-03", "last": "2026-08-03"}
    assert {"bills", "safe_to_spend", "budgets", "alerts"} <= set(d)
    assert api.get("/analytics/dashboard", params={"month": "2026-13"}).status_code == 422
