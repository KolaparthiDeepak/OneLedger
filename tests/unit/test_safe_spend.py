"""Safe-to-spend arithmetic (pure domain)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from oneledger_domain.enums import RecurrenceCadence
from oneledger_domain.safe_spend import Obligation, compute, roll_forward

TODAY = date(2026, 9, 29)


def test_obligations_before_next_income_are_reserved():
    obs = [
        Obligation("Rent", date(2026, 10, 5), D("28000"), "recurring", "r"),
        Obligation("Netflix", date(2026, 9, 30), D("649"), "recurring", "n"),
        Obligation("Card bill", date(2026, 10, 12), D("16000"), "card", "c"),
    ]
    r = compute(D("60000"), TODAY, date(2026, 10, 10), obs)
    assert [o.label for o in r.obligations] == ["Netflix", "Rent"]  # the card bill falls after payday
    assert r.obligations_total == D("28649") and r.safe_total == D("31351")
    assert r.days_left == 11 and r.per_day == D("2850.09")  # rounded down, never up
    assert not r.shortfall


def test_shortfall_gives_zero_per_day():
    r = compute(D("1000"), TODAY, date(2026, 10, 1), [Obligation("EMI", date(2026, 9, 30), D("21500"), "loan", "l")])
    assert r.shortfall and r.per_day == 0 and r.safe_total == D("-20500")


def test_without_income_it_looks_a_month_ahead_and_says_so():
    r = compute(D("3100"), TODAY, None, [])
    assert r.until == date(2026, 10, 30) and r.per_day == D("100.00")
    assert any("No regular income" in a for a in r.assumptions)


def test_roll_forward_skips_missed_dates():
    assert roll_forward(date(2026, 9, 1), RecurrenceCadence.MONTHLY, TODAY) == date(2026, 10, 1)
    assert roll_forward(date(2026, 9, 30), RecurrenceCadence.MONTHLY, TODAY) == date(2026, 9, 30)
    assert roll_forward(date(2026, 9, 22), RecurrenceCadence.WEEKLY, TODAY) == date(2026, 10, 6)
