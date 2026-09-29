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


def test_safe_to_spend_does_not_count_on_overdue_salary_or_cash_withdrawals(api):
    # Review findings N1 and N3: the salary expected last month never came, and ATM withdrawals go
    # into your own cash wallet, so neither may inflate or reserve the figure.
    months = [_month_start(-4), _month_start(-3), _month_start(-2)]
    bank = account(api, "HDFC", opening_date=_iso(months[0] - timedelta(days=1)), opening_balance="10000.00")
    rows = []
    for i, m in enumerate(months):
        rows.append(f"{_d(m)},NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY,S{i},,125000.00,1.00")
        rows.append(f"{_d(m + timedelta(days=19))},ATM CASH WDL MG ROAD,A{i},4000.00,,1.00")
    import_csv(api, bank, csv_bytes(rows))
    api.ok(api.post("/recurring/detect"))
    api.ok(
        api.post(
            "/accounts",
            json={
                "name": "Cash",
                "kind": "CASH",
                "opening_date": _iso(TODAY - timedelta(days=1)),
                "opening_balance": "0",
            },
        ),
        201,
    )
    bills = api.ok(api.get("/insights/upcoming", params={"days": 62}))
    cash = [b for b in bills if "ATM" in b["label"]]
    assert cash and all(b["kind"] == "cash" and "cash wallet" in b["detail"] for b in cash)
    safe = api.ok(api.get("/insights/safe-to-spend"))
    assert safe["available"] and safe["income_overdue"]
    assert safe["until_label"] is None and safe["days_left"] == 31
    assert not any("ATM" in o["label"] for o in safe["obligations"])
    assert any("hasn't arrived" in n for n in safe["assumptions"])
    assert not any(a["key"].startswith("bill:cash") for a in api.ok(api.get("/insights/alerts")))


def test_dashboard_compares_a_month_in_progress_with_the_same_days(api):
    # Review finding N2: month to date is compared with the same days of last month.
    account(api, "HDFC", opening_date=_iso(_month_start(-1) - timedelta(days=1)), opening_balance="1000.00")
    d = api.ok(api.get("/analytics/dashboard"))
    start, prev_start = _month_start(0), _month_start(-1)
    expected_end = min(prev_start + timedelta(days=(TODAY - start).days + 1), start)
    assert d["comparison"] == {
        "start": _iso(prev_start),
        "end_exclusive": _iso(expected_end),
        "partial": expected_end < start,
    }
    past = api.ok(api.get("/analytics/dashboard", params={"month": prev_start.strftime("%Y-%m")}))
    assert past["comparison"]["partial"] is False
