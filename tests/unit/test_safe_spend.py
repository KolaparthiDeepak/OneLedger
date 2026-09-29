"""Safe-to-spend arithmetic (pure domain)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from oneledger_domain.enums import RecurrenceCadence
from oneledger_domain.safe_spend import Obligation, compute, next_income_date, roll_forward

TODAY = date(2026, 9, 29)


def test_obligations_before_next_income_are_reserved():
    obs = [
        Obligation("Rent", date(2026, 10, 5), D("28000"), "recurring", "r"),
        Obligation("Netflix", date(2026, 9, 30), D("649"), "recurring", "n"),
        Obligation("Card bill", date(2026, 10, 16), D("16000"), "card", "c"),
    ]
    r = compute(D("60000"), TODAY, date(2026, 10, 10), obs)
    assert [o.label for o in r.obligations] == ["Netflix", "Rent"]  # the card bill falls well after payday
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


def test_bills_just_after_payday_are_kept_aside():
    # Review finding N1: salary on 1 Oct, rent and EMI on 5 Oct. Without the buffer this showed
    # the whole balance spread over two days.
    obs = [
        Obligation("Rent", date(2026, 10, 5), D("28000"), "recurring", "r"),
        Obligation("EMI", date(2026, 10, 5), D("21500"), "loan", "l"),
        Obligation("Card bill", date(2026, 10, 21), D("16000"), "card", "c"),
    ]
    r = compute(D("129494.62"), TODAY, date(2026, 10, 1), obs)
    assert [o.label for o in r.obligations] == ["EMI", "Rent"]
    assert r.safe_total == D("79994.62") and r.days_left == 2
    assert any("after payday" in a for a in r.assumptions)


def test_overdue_income_is_not_counted_on():
    # Expected on 1 Sep and not seen by 29 Sep: no longer rolled forward to 1 Oct.
    assert next_income_date(date(2026, 9, 1), RecurrenceCadence.MONTHLY, TODAY) is None
    # A day or two late is still expected on its usual schedule.
    assert next_income_date(date(2026, 9, 27), RecurrenceCadence.MONTHLY, TODAY) == date(2026, 10, 27)
    assert next_income_date(date(2026, 10, 1), RecurrenceCadence.MONTHLY, TODAY) == date(2026, 10, 1)
    r = compute(D("3100"), TODAY, None, [], no_income_note="Salary was expected on 01 Sep 2026 and hasn't arrived.")
    assert r.until == date(2026, 10, 30) and r.per_day == D("100.00")
    assert r.assumptions == ["Salary was expected on 01 Sep 2026 and hasn't arrived."]
