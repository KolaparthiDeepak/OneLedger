from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from oneledger_domain import loans as ln
from oneledger_domain import networth as nw
from oneledger_domain.dedup import Candidate, Decision, Existing, detect_duplicates
from oneledger_domain.enums import AccountKind, AccountNature, AllocationEffect, RecurrenceCadence
from oneledger_domain.money import MoneyParseError, format_inr, parse_amount, to_decimal, validate_precision
from oneledger_domain.periods import DateRange, add_months, last_month, resolve_period
from oneledger_domain.recurrence import Occurrence, detect_recurring
from oneledger_domain.reporting import AllocationFact, summarize
from oneledger_domain.text import extract_reference, normalize_description
from oneledger_domain.transfers import Leg, match_transfers


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,23,456.78", (D("123456.78"), None)),
        ("(1,234.00)", (D("-1234.00"), None)),
        ("1,234.00 Dr", (D("1234.00"), "DR")),
        ("500 CR", (D("500"), "CR")),
        ("₹ 2,450.50", (D("2450.50"), None)),
        ("-99.99", (D("-99.99"), None)),
        ("12.00-", (D("-12.00"), None)),
        ("", None),
        ("-", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_amount_rejects_garbage():
    with pytest.raises(MoneyParseError):
        parse_amount("12a.00")


def test_no_float_money():
    with pytest.raises(TypeError):
        to_decimal(1.5)
    with pytest.raises(MoneyParseError):
        validate_precision(D("1.005"), "INR")


def test_format_inr():
    assert format_inr(D("1234567.5")) == "12,34,567.50"
    assert format_inr(D("-999")) == "-999.00"


def test_f19_periods_month_end_and_leap():
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert last_month("Asia/Kolkata", date(2026, 3, 1)) == DateRange(date(2026, 2, 1), date(2026, 3, 1))
    assert resolve_period("august", "Asia/Kolkata", date(2026, 9, 24)).start == date(2026, 8, 1)
    assert resolve_period("december", "Asia/Kolkata", date(2026, 9, 24)).start == date(2025, 12, 1)
    assert DateRange(date(2026, 9, 1), date(2026, 10, 1)).previous() == DateRange(date(2026, 8, 1), date(2026, 9, 1))


def test_reference_extraction():
    assert extract_reference("UPI/DR/612345678901/SWIGGY/YESB") == "612345678901"
    assert normalize_description("  Swiggy*12345 ") == "SWIGGY 12345"


def test_dedup_multiplicity_and_balance():
    acct = uuid.uuid4()
    e1 = Existing(uuid.uuid4(), date(2026, 8, 5), D("-100"), "INR", "POS SHOP", None, D("900"))
    cands = [
        Candidate(0, date(2026, 8, 5), D("-100"), "INR", "POS SHOP", None, D("900")),
        Candidate(1, date(2026, 8, 5), D("-100"), "INR", "POS SHOP", None, D("800")),
    ]
    out = detect_duplicates(acct, cands, [e1])
    assert out[0].decision == Decision.DUPLICATE and out[1].decision == Decision.NEW


def test_dedup_reference_unique_vs_ambiguous():
    acct = uuid.uuid4()
    e = [
        Existing(uuid.uuid4(), date(2026, 8, 5), D("-50"), "INR", "A", "REF123456", None),
        Existing(uuid.uuid4(), date(2026, 8, 6), D("-50"), "INR", "B", "REF123456", None),
    ]
    out = detect_duplicates(acct, [Candidate(0, date(2026, 8, 5), D("-50"), "INR", "X", "REF123456")], e)
    assert out[0].decision == Decision.POSSIBLE_DUPLICATE


def _leg(acct, amt, d, desc="", ref=None, kind=AccountKind.BANK_SAVINGS, last4=None):
    return Leg(uuid.uuid4(), uuid.uuid4(), acct, kind, last4, D(amt), "INR", d, desc, ref)


def test_transfer_matching_strength():
    a, b = uuid.uuid4(), uuid.uuid4()
    strong = match_transfers(
        [
            _leg(a, "-50000", date(2026, 8, 10), ref="UTR12345678"),
            _leg(b, "50000", date(2026, 8, 11), ref="UTR12345678"),
        ]
    )
    assert strong[0].auto_confirm
    weak = match_transfers([_leg(a, "-50000", date(2026, 8, 10)), _leg(b, "50000", date(2026, 8, 10))])
    assert not weak[0].auto_confirm
    ambiguous = match_transfers(
        [
            _leg(a, "-500", date(2026, 8, 10), ref="R123456789"),
            _leg(b, "500", date(2026, 8, 10), ref="R123456789"),
            _leg(uuid.uuid4(), "500", date(2026, 8, 10), ref="R123456789"),
        ]
    )
    assert not any(p.auto_confirm for p in ambiguous)
    far = match_transfers([_leg(a, "-500", date(2026, 8, 1)), _leg(b, "500", date(2026, 8, 9))])
    assert far == []


def test_summary_definitions():
    s = summarize(
        [
            AllocationFact(AllocationEffect.INCOME, D("100000")),
            AllocationFact(AllocationEffect.EXPENSE, D("-650")),
            AllocationFact(AllocationEffect.EXPENSE, D("200")),
            AllocationFact(AllocationEffect.TRANSFER, D("-50000")),
            AllocationFact(AllocationEffect.LOAN_PRINCIPAL, D("-8000")),
            AllocationFact(AllocationEffect.UNCLASSIFIED, D("-10")),
        ]
    )
    assert s.net_expenses == D("450") and s.income == D("100000") and s.loan_principal_paid == D("8000")
    assert s.unclassified_outflow == D("10") and s.provisional


def test_f16_loans():
    zero = ln.build_schedule(D("12000"), D("0"), 12, date(2026, 1, 31))
    assert len(zero.rows) == 12 and all(r.interest == 0 for r in zero.rows) and zero.rows[-1].closing_principal == 0
    assert zero.rows[1].due_date == date(2026, 2, 28)
    s = ln.build_schedule(D("1000000"), D("8.5"), 240, date(2026, 1, 5))
    assert s.rows[0].emi == D("8678.23") and s.rows[-1].closing_principal == 0
    assert s.total_principal == D("1000000")
    with pytest.raises(ln.LoanModelError):
        ln.build_schedule(D("100000"), D("12"), 12, date(2026, 1, 1), emi=D("500"))  # negative amortization
    with pytest.raises(ln.LoanModelError):
        ln.compute_emi(D("1000"), D("5"), 0)
    rc = ln.build_schedule(
        D("1000000"), D("8.5"), 240, date(2026, 1, 5), rate_changes=[ln.RateChange(date(2027, 1, 5), D("9.5"))]
    )
    assert len(rc.rows) > 240  # KEEP_EMI extends tenure when rates rise
    imp = ln.simulate_prepayment(
        D("1000000"), D("8.5"), 240, date(2026, 1, 5), ln.Prepayment(date(2027, 1, 5), D("200000"))
    )
    assert imp.interest_saved > 0 and imp.months_saved > 0
    p, i = ln.split_emi(D("800000"), D("9"), D("10000"))
    assert p + i == D("10000") and i == D("6000.00")


def test_f27_net_worth_cutoff_and_compare():
    obs = [nw.Observation(D("100"), date(2026, 8, 1), "a"), nw.Observation(D("200"), date(2026, 9, 1), "b")]
    assert nw.select_latest(obs, date(2026, 8, 15)).value == D("100")
    assert nw.select_latest(obs, date(2026, 7, 1)) is None
    comps = [nw.Component("x", "X", AccountNature.ASSET, "INR", "BANK", obs[0])]
    cur = nw.compute_net_worth(comps, date(2026, 8, 20))
    prev = nw.compute_net_worth([nw.Component("x", "X", AccountNature.ASSET, "INR", "BANK", None)], date(2026, 7, 1))
    assert nw.compare(cur, prev).available is False


def test_recurrence_monthly_subscription():
    acct = uuid.uuid4()
    occ = [
        Occurrence(uuid.uuid4(), acct, date(2026, m, 12), D("-649"), "NETFLIX.COM SUBSCRIPTION") for m in range(3, 9)
    ]
    pats = detect_recurring(occ)
    assert len(pats) == 1
    p = pats[0]
    assert p.cadence == RecurrenceCadence.MONTHLY and p.expected_next_date == date(2026, 9, 12)
    assert p.recurrence_type.value == "SUBSCRIPTION"
