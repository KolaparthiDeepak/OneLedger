"""Phone alerts through ntfy: each alert once, bills again on the day, quiet hours, failures retried."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from oneledger_api.services import notifications as n
from oneledger_db.models import User
from oneledger_db.session import owner_session
from oneledger_domain.periods import add_months
from sqlalchemy import text

from .helpers import account


@pytest.fixture()
def pushes(monkeypatch):  # type: ignore[no-untyped-def]
    """Every message published to ntfy, and a switch to make the server fail."""
    sent: list[dict] = []
    state = {"status": 200}

    def handle(request: httpx.Request) -> httpx.Response:
        if state["status"] == 200:
            sent.append(json.loads(request.content))
        return httpx.Response(state["status"])

    monkeypatch.setattr(n, "TRANSPORT", httpx.MockTransport(handle))
    return sent, state


def _owner(ctx):  # type: ignore[no-untyped-def]
    with owner_session(ctx.sessions, None) as db:
        return db.execute(text("SELECT id FROM oneledger.auth_user_by_email('owner@example.com')")).scalar_one()


def _check(ctx, owner, hour: int) -> int:  # type: ignore[no-untyped-def]
    with owner_session(ctx.sessions, owner) as db:
        tz = ZoneInfo(db.get(User, owner).timezone)
        now = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0)
        return n.check_and_send(ctx, db, owner, now=now)


def _bills(api, owner_tz: str) -> None:  # type: ignore[no-untyped-def]
    today = datetime.now(ZoneInfo(owner_tz)).date()
    account(api, "HDFC", opening_date=(today - timedelta(days=1)).isoformat(), opening_balance="50000.00")
    due = today + timedelta(days=2)
    api.ok(
        api.post(
            "/loans",
            json={
                "lender": "SBI",
                "loan_type": "Home Loan",
                "original_principal": "1000000.00",
                "opening_outstanding": "800000.00",
                "opening_date": (today - timedelta(days=1)).isoformat(),
                "start_date": "2020-01-01",
                "first_emi_date": add_months(due, -60).isoformat(),
                "tenure_months": 240,
                "annual_rate_percent": "8.5",
                "emi_amount": "21500.00",
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
                "outstanding_as_of": (today - timedelta(days=20)).isoformat(),
            },
        ),
        201,
    )
    api.ok(
        api.post(
            f"/cards/{card['id']}/statements",
            json={
                "period_start": (today - timedelta(days=40)).isoformat(),
                "period_end": (today - timedelta(days=10)).isoformat(),
                "statement_balance": "12000",
                "minimum_due": "600",
                "due_date": today.isoformat(),
            },
        ),
        201,
    )


def test_bills_reach_the_phone_once_and_again_on_the_day(ctx, api, pushes):  # type: ignore[no-untyped-def]
    sent, _ = pushes
    owner = _owner(ctx)
    s = api.ok(api.get("/notifications/settings"))
    assert s["enabled"] is False and s["topic"] is None
    _bills(api, api.ok(api.get("/me"))["timezone"])
    assert _check(ctx, owner, 12) == 0  # off until you turn it on

    s = api.ok(api.put("/notifications/settings", json={"enabled": True}))
    assert s["topic"].startswith("oneledger-") and s["subscribe_url"] == f"https://ntfy.sh/{s['topic']}"
    assert _check(ctx, owner, 23) == 0 and sent == []  # quiet hours: held back, not dropped

    assert _check(ctx, owner, 12) == 2
    by_title = {m["title"]: m for m in sent}
    card = next(m for t, m in by_title.items() if t.startswith("Due today"))
    emi = next(m for t, m in by_title.items() if "EMI" in t)
    assert card["priority"] == 5 and "₹12,000.00" in card["message"] and card["topic"] == s["topic"]
    assert emi["priority"] == 4 and emi["title"].startswith("Due soon") and "₹21,500.00" in emi["message"]
    assert _check(ctx, owner, 13) == 0 and len(sent) == 2  # each alert once


def test_amounts_can_be_left_out(ctx, api, pushes):  # type: ignore[no-untyped-def]
    sent, _ = pushes
    owner = _owner(ctx)
    _bills(api, api.ok(api.get("/me"))["timezone"])
    api.ok(api.put("/notifications/settings", json={"enabled": True, "show_amounts": False, "quiet_hours": False}))
    assert _check(ctx, owner, 23) == 2
    assert {m["title"] for m in sent} == {"OneLedger: a bill is due"}
    assert all("₹" not in m["message"] and "SBI" not in m["message"] for m in sent)


def test_a_failed_send_is_recorded_and_retried(ctx, api, pushes):  # type: ignore[no-untyped-def]
    sent, state = pushes
    owner = _owner(ctx)
    _bills(api, api.ok(api.get("/me"))["timezone"])
    api.ok(api.put("/notifications/settings", json={"enabled": True}))
    state["status"] = 500
    assert _check(ctx, owner, 12) == 0
    assert "HTTP 500" in api.ok(api.get("/notifications/settings"))["last_error"]
    state["status"] = 200
    assert _check(ctx, owner, 12) == 2 and len(sent) == 2
    assert api.ok(api.get("/notifications/settings"))["last_error"] is None


def test_test_alert_and_new_topic(ctx, api, pushes):  # type: ignore[no-untyped-def]
    sent, _ = pushes
    first = api.ok(api.post("/notifications/test"))
    assert sent[-1]["title"] == "OneLedger test alert" and sent[-1]["topic"] == first["topic"]
    second = api.ok(api.post("/notifications/topic"))
    assert second["topic"] != first["topic"]
    bad = api.put("/notifications/settings", json={"server_url": "http://ntfy.example.com"})
    assert bad.status_code == 422
    assert (
        api.ok(api.put("/notifications/settings", json={"server_url": "https://ntfy.example.com/"}))["server_url"]
        == "https://ntfy.example.com"
    )


def test_api_tokens_cannot_read_the_topic(ctx, api, client):  # type: ignore[no-untyped-def]
    token = api.ok(api.post("/tokens", json={"label": "mcp"}), 201)["token"]
    r = client.get("/api/v1/notifications/settings", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 403)
