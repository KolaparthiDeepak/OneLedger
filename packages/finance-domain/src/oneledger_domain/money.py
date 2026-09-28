"""Decimal money helpers. Binary floats are never accepted for monetary values."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 minor units for currencies we expect; unknown currencies default to 2.
_MINOR_UNITS = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2, "AED": 2, "SGD": 2, "JPY": 0, "KWD": 3, "BHD": 3}

ZERO = Decimal("0")


class MoneyParseError(ValueError):
    pass


def minor_units(currency: str) -> int:
    return _MINOR_UNITS.get(currency.upper(), 2)


def quantum(currency: str) -> Decimal:
    return Decimal(1).scaleb(-minor_units(currency))


def quantize(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(quantum(currency), rounding=ROUND_HALF_UP)


def validate_precision(amount: Decimal, currency: str) -> None:
    """Reject amounts with more decimal places than the currency supports."""
    if not amount.is_finite():
        raise MoneyParseError("amount must be finite")
    exponent = amount.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > minor_units(currency):
        raise MoneyParseError(f"amount has more than {minor_units(currency)} decimal places for {currency}")


def to_decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise TypeError("floating-point money is not accepted; pass a string or Decimal")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyParseError("invalid decimal amount") from exc
    raise TypeError(f"unsupported money type {type(value).__name__}")


_CURRENCY_TOKENS = re.compile(r"(₹|rs\.?|inr|\$|usd|€|eur|£|gbp)", re.IGNORECASE)
_DRCR = re.compile(r"\s*\b(dr|cr|debit|credit)\.?\s*$", re.IGNORECASE)
_VALID_NUMBER = re.compile(r"^\d{1,3}(,\d{2,3})*(\.\d+)?$|^\d+(\.\d+)?$|^\.\d+$")


def parse_amount(raw: str | None) -> tuple[Decimal, str | None] | None:
    """Parse statement amount text.

    Returns ``(magnitude_with_sign, drcr_marker)`` or ``None`` for blank cells. Handles Indian
    digit grouping (``1,23,456.78``), parentheses negatives, leading/trailing minus signs,
    currency symbols and ``Dr``/``Cr`` suffixes. The marker is ``"DR"``/``"CR"`` when present;
    the caller decides sign semantics from the column mapping.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "-", "--", "—", "NA", "N/A", "nil", "Nil"):
        return None
    marker: str | None = None
    m = _DRCR.search(text)
    if m:
        marker = "DR" if m.group(1).lower().startswith("d") else "CR"
        text = text[: m.start()].strip()
    text = _CURRENCY_TOKENS.sub("", text).strip()
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text.startswith("-"):
        negative, text = not negative, text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()
    if text.endswith("-"):
        negative, text = not negative, text[:-1].strip()
    text = text.replace(" ", "")
    if not _VALID_NUMBER.match(text):
        raise MoneyParseError("unrecognised amount format")
    value = Decimal(text.replace(",", ""))
    return (-value if negative else value), marker


def format_inr(amount: Decimal) -> str:
    """Indian digit grouping, e.g. 1234567.5 -> '12,34,567.50'. Used in server-side text only."""
    q = quantize(amount, "INR")
    sign = "-" if q < 0 else ""
    whole, _, frac = f"{abs(q):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{frac}"
