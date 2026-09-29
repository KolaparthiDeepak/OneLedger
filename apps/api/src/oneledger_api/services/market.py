"""Mutual fund prices from AMFI's public daily NAV file.

Only the public file is downloaded; nothing about you or your holdings is sent. Prices are fetched
when you ask ("Update price"), cached for a few hours, and stored as dated valuations labelled AMFI.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import httpx
from oneledger_db.models import Investment, InvestmentValuation
from oneledger_domain.enums import InstrumentType
from oneledger_shared.errors import ValidationFailed
from sqlalchemy.orm import Session

from .audit import audit, bump_ledger_revision

AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
CACHE_SECONDS = 6 * 3600


@dataclass(frozen=True, slots=True)
class Nav:
    scheme_code: str
    isins: tuple[str, ...]
    name: str
    nav: Decimal
    nav_date: date


_cache: tuple[float, dict[str, Nav]] | None = None


def parse_amfi(text: str) -> dict[str, Nav]:
    """Rows look like ``Scheme Code;ISIN Growth;ISIN Reinvest;Scheme Name;NAV;DD-Mon-YYYY``.
    Section headings and blank lines are skipped; rows without a numeric NAV are ignored."""
    out: dict[str, Nav] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        try:
            nav = Decimal(parts[4])
            on = datetime.strptime(parts[5], "%d-%b-%Y").replace(tzinfo=UTC).date()
        except (InvalidOperation, ValueError):
            continue
        isins = tuple(p for p in (parts[1], parts[2]) if p and p != "-")
        row = Nav(parts[0], isins, parts[3], nav, on)
        out[row.scheme_code] = row
        for i in isins:
            out[i] = row
    return out


def _download() -> str:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        r = client.get(AMFI_URL)
        r.raise_for_status()
        return r.text


def load_navs(fetch: Callable[[], str] | None = None) -> dict[str, Nav]:
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        text = (fetch or _download)()
    except httpx.HTTPError as exc:
        raise ValidationFailed("Could not reach AMFI for prices. Try again later.", code="PRICES_UNAVAILABLE") from exc
    navs = parse_amfi(text)
    if not navs:
        raise ValidationFailed("AMFI returned no prices. Try again later.", code="PRICES_UNAVAILABLE")
    _cache = (time.monotonic(), navs)
    return navs


def search(q: str, limit: int = 20) -> list[dict[str, str]]:
    words = [w for w in q.upper().split() if w]
    if not words:
        return []
    seen: set[str] = set()
    out = []
    for n in load_navs().values():
        if n.scheme_code in seen:
            continue
        name = n.name.upper()
        if all(w in name for w in words):
            seen.add(n.scheme_code)
            out.append(
                {"scheme_code": n.scheme_code, "name": n.name, "nav": str(n.nav), "nav_date": n.nav_date.isoformat()}
            )
            if len(out) >= limit:
                break
    return out


def refresh_price(
    db: Session, owner_id: uuid.UUID, inv: Investment, units: Decimal | None, actor: str
) -> InvestmentValuation:
    if inv.instrument_type != InstrumentType.MUTUAL_FUND or not inv.identifier:
        raise ValidationFailed("Add the AMFI scheme code or ISIN to this fund first.", code="IDENTIFIER_REQUIRED")
    if inv.valuation_mode != "UNITS" or units is None or units <= 0:
        raise ValidationFailed("Record the units you hold to value this fund.", code="UNITS_REQUIRED")
    nav = load_navs().get(inv.identifier.strip().upper()) or load_navs().get(inv.identifier.strip())
    if nav is None:
        raise ValidationFailed("That scheme code or ISIN is not in AMFI's list.", code="SCHEME_NOT_FOUND")
    v = InvestmentValuation(
        owner_id=owner_id,
        investment_id=inv.id,
        valuation_date=nav.nav_date,
        unit_price=nav.nav,
        total_value=(units * nav.nav).quantize(Decimal("0.01")),
        currency=inv.currency,
        source="AMFI",
        is_estimated=False,
    )
    db.add(v)
    db.flush()
    bump_ledger_revision(db, owner_id)
    audit(db, owner_id, actor, "investment.price_update", "investment", inv.id, ["valuation"])
    return v
