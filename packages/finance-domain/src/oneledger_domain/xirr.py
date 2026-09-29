"""Annualised return (XIRR) of a holding from dated cash flows.

Money you put in is negative, money you took out (and today's value) is positive. The rate is found
by bisection, so it never diverges; it is a display figure, never used in any total.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

XIRR_VERSION = "xirr-v1"


def _npv(rate: float, flows: list[tuple[float, float]]) -> float:
    total = 0.0
    for t, cf in flows:
        total += cf / float((1 + rate) ** t)
    return total


def xirr(cashflows: list[tuple[date, Decimal]]) -> Decimal | None:
    """Annual rate as a fraction (0.1234 = 12.34%), or None when it cannot be determined."""
    flows = [(d, float(a)) for d, a in cashflows if a != 0]
    if len(flows) < 2 or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    start = min(d for d, _ in flows)
    span = (max(d for d, _ in flows) - start).days
    if span < 1:
        return None
    timed = [((d - start).days / 365.0, a) for d, a in flows]
    lo, hi = -0.9999, 100.0
    f_lo, f_hi = _npv(lo, timed), _npv(hi, timed)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = _npv(mid, timed)
        if abs(f_mid) < 1e-7 or hi - lo < 1e-10:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return Decimal(str(round((lo + hi) / 2, 6)))
