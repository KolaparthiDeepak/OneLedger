"""Reporting periods. All ranges are half-open: ``[start, end_exclusive)``."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end_exclusive: date

    def __post_init__(self) -> None:
        if self.end_exclusive <= self.start:
            raise ValueError("end_exclusive must be after start")

    @property
    def days(self) -> int:
        return (self.end_exclusive - self.start).days

    def contains(self, d: date) -> bool:
        return self.start <= d < self.end_exclusive

    def previous(self) -> DateRange:
        """Previous period of the same shape (calendar months are preserved)."""
        if is_month_aligned(self):
            months = month_diff(self.start, self.end_exclusive)
            return DateRange(add_months(self.start, -months), self.start)
        return DateRange(self.start - timedelta(days=self.days), self.start)


def add_months(d: date, months: int) -> date:
    """Calendar month arithmetic clamped to month end (Jan 31 + 1 month -> Feb 28/29)."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_diff(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def is_month_aligned(r: DateRange) -> bool:
    return r.start.day == 1 and r.end_exclusive.day == 1


def month_range(year: int, month: int) -> DateRange:
    start = date(year, month, 1)
    return DateRange(start, add_months(start, 1))


def today_in(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def this_month(tz: str, today: date | None = None) -> DateRange:
    t = today or today_in(tz)
    return month_range(t.year, t.month)


def last_month(tz: str, today: date | None = None) -> DateRange:
    """Previous calendar month in the owner's timezone."""
    t = today or today_in(tz)
    first = date(t.year, t.month, 1)
    return DateRange(add_months(first, -1), first)


def trailing_days(n: int, tz: str, today: date | None = None) -> DateRange:
    t = today or today_in(tz)
    return DateRange(t - timedelta(days=n - 1), t + timedelta(days=1))


def inclusive(start: date, end_inclusive: date) -> DateRange:
    return DateRange(start, end_inclusive + timedelta(days=1))


def months_between(r: DateRange) -> list[DateRange]:
    out: list[DateRange] = []
    cur = date(r.start.year, r.start.month, 1)
    while cur < r.end_exclusive:
        nxt = add_months(cur, 1)
        out.append(DateRange(max(cur, r.start), min(nxt, r.end_exclusive)))
        cur = nxt
    return out


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m} | {
    m.lower(): i for i, m in enumerate(calendar.month_name) if m
}


def resolve_period(expression: str, tz: str, today: date | None = None) -> DateRange:
    """Resolve simple natural-language periods used by the API/MCP.

    Supported: ``this_month``, ``last_month``, ``last_30_days``/``last_N_days``, ``this_year``,
    ``YYYY-MM``, ``YYYY``, and month names (most recent occurrence not in the future).
    """
    t = today or today_in(tz)
    e = expression.strip().lower().replace(" ", "_")
    if e in ("this_month", "month", "current_month"):
        return this_month(tz, t)
    if e in ("last_month", "previous_month"):
        return last_month(tz, t)
    if e in ("this_year", "ytd", "year_to_date"):
        return DateRange(date(t.year, 1, 1), t + timedelta(days=1))
    if e == "last_year":
        return DateRange(date(t.year - 1, 1, 1), date(t.year, 1, 1))
    m = re.fullmatch(r"last_(\d{1,4})_days", e)
    if m:
        return trailing_days(int(m.group(1)), tz, t)
    m = re.fullmatch(r"(\d{4})-(\d{2})", e)
    if m:
        return month_range(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d{4})", e)
    if m:
        y = int(m.group(1))
        return DateRange(date(y, 1, 1), date(y + 1, 1, 1))
    m = re.fullmatch(r"([a-z]+)(?:_(\d{4}))?", e)
    if m and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else (t.year if month <= t.month else t.year - 1)
        return month_range(year, month)
    raise ValueError(f"unsupported period expression: {expression!r}")
