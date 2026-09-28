"""Column mapping, date-format detection and row normalization.

Canonical sign: from the account holder's perspective a statement *debit* reduces equity and a
*credit* increases it, for bank and card statements alike. So ``amount = credit - debit``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from oneledger_domain.enums import PaymentChannel
from oneledger_domain.money import MoneyParseError, parse_amount, validate_precision
from oneledger_domain.text import clean_description, detect_channel, extract_reference, normalize_reference
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tabular import RawTable

MAPPING_VERSION = 1

DATE_FORMATS: dict[str, str] = {
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD/MM/YY": "%d/%m/%y",
    "MM/DD/YY": "%m/%d/%y",
    "DD-MM-YY": "%d-%m-%y",
    "DD-MMM-YYYY": "%d-%b-%Y",
    "DD MMM YYYY": "%d %b %Y",
    "DD-MMM-YY": "%d-%b-%y",
    "DD MMM YY": "%d %b %y",
    "DD/MMM/YYYY": "%d/%b/%Y",
    "YYYY/MM/DD": "%Y/%m/%d",
}


def _parse_with(fmt: str, text: str) -> date | None:
    t = text.strip()
    if not t:
        return None
    # Spreadsheet datetimes such as "2026-08-01 00:00:00" or ISO timestamps.
    t = re.sub(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$", "", t)
    try:
        return datetime.strptime(t.title() if "%b" in DATE_FORMATS[fmt] else t, DATE_FORMATS[fmt]).date()  # noqa: DTZ007
    except ValueError:
        return None


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?)?$")


def parse_date(fmt: str, text: str) -> date | None:
    """Parse with the chosen format; real spreadsheet dates (ISO text) are unambiguous and always accepted."""
    parsed = _parse_with(fmt, text)
    if parsed is None and _ISO_DATE.match(text.strip()):
        parsed = _parse_with("YYYY-MM-DD", text)
    return parsed


def detect_date_formats(values: list[str]) -> tuple[list[str], bool]:
    """Return formats that parse every non-empty value, and whether the choice is ambiguous.

    Ambiguous means at least two viable formats yield different dates for some value
    (e.g. ``03/04/2026``); the user must then choose explicitly.
    """
    # Footnotes and "Total" lines in the date column contain no digits; they are not dates.
    samples = [v for v in values if v and v.strip() and any(ch.isdigit() for ch in v)][:500]
    if not samples:
        return [], False
    viable = [f for f in DATE_FORMATS if all(_parse_with(f, v) is not None for v in samples)]
    if len(viable) <= 1:
        return viable, False
    first = viable[0]
    ambiguous = any(any(_parse_with(f, v) != _parse_with(first, v) for v in samples) for f in viable[1:])
    return viable, ambiguous


class ColumnMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_column: str
    date_format: str
    value_date_column: str | None = None
    description_columns: list[str] = Field(min_length=1, max_length=4)
    amount_mode: Literal["split", "signed", "drcr_column"] = "split"
    debit_column: str | None = None
    credit_column: str | None = None
    amount_column: str | None = None
    signed_positive_means: Literal["credit", "debit"] = "credit"
    drcr_column: str | None = None
    reference_column: str | None = None
    balance_column: str | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    skip_rows_matching: list[str] = Field(default_factory=lambda: ["opening balance", "closing balance", "total"])

    @model_validator(mode="after")
    def _validate(self) -> ColumnMapping:
        if self.date_format not in DATE_FORMATS:
            raise ValueError("unsupported date_format")
        if self.amount_mode == "split" and not (self.debit_column and self.credit_column):
            raise ValueError("split mode requires debit_column and credit_column")
        if self.amount_mode in ("signed", "drcr_column") and not self.amount_column:
            raise ValueError("amount_column is required")
        if self.amount_mode == "drcr_column" and not self.drcr_column:
            raise ValueError("drcr_column is required")
        self.currency = self.currency.upper()
        return self

    def referenced_columns(self) -> list[str]:
        cols = [self.date_column, *self.description_columns]
        cols += [
            c
            for c in (
                self.value_date_column,
                self.debit_column,
                self.credit_column,
                self.amount_column,
                self.drcr_column,
                self.reference_column,
                self.balance_column,
            )
            if c
        ]
        return cols


_SUGGEST = {
    "date": re.compile(
        r"^(txn |transaction |tran |posting |post |booking )?(date|dt)\.?$|^txn date|^tran date"
        r"|^posted( on| date)?$|^txn posted$",
        re.I,
    ),
    "value_date": re.compile(r"value ?d(a)?t(e)?", re.I),
    "description": re.compile(r"narration|description|particulars|details|remarks|transaction details", re.I),
    "debit": re.compile(r"withdrawal|debit|dr\b|paid out|money out", re.I),
    "credit": re.compile(r"deposit|credit|cr\b|paid in|money in", re.I),
    "amount": re.compile(r"^amount|amount \(|txn amount|transaction amount", re.I),
    "drcr": re.compile(r"^(dr ?/ ?cr|cr ?/ ?dr|type|debit/credit)$", re.I),
    "reference": re.compile(r"ref|chq|cheque|utr", re.I),
    "balance": re.compile(r"balance|\bbal\b", re.I),
}


def suggest_mapping(table: RawTable) -> dict[str, object]:
    """Heuristic suggestion shown to the user for confirmation; never applied silently."""
    found: dict[str, str] = {}
    for h in table.headers:
        # A "Dr/Cr" marker column also contains "Dr" and "Cr"; claim it first so it is never
        # mistaken for a debit or credit amount column.
        if "drcr" not in found and _SUGGEST["drcr"].search(h.strip()):
            found["drcr"] = h
            continue
        for key, rx in _SUGGEST.items():
            if key in found or key == "drcr":
                continue
            if key == "date" and _SUGGEST["value_date"].search(h):
                continue
            if key in ("debit", "credit") and _SUGGEST["balance"].search(h):
                continue
            if key == "reference" and _SUGGEST["description"].search(h):
                continue
            if rx.search(h):
                found[key] = h
                break
    suggestion: dict[str, object] = {"currency": "INR"}
    if "date" in found:
        suggestion["date_column"] = found["date"]
        col = table.headers.index(found["date"])
        formats, ambiguous = detect_date_formats([r[col] for r in table.rows])
        suggestion["date_format_candidates"] = formats
        suggestion["date_format_ambiguous"] = ambiguous
        if formats and not ambiguous:
            suggestion["date_format"] = formats[0]
    if "value_date" in found:
        suggestion["value_date_column"] = found["value_date"]
    if "description" in found:
        suggestion["description_columns"] = [found["description"]]
    if "debit" in found and "credit" in found and found["debit"] != found["credit"]:
        suggestion.update(amount_mode="split", debit_column=found["debit"], credit_column=found["credit"])
    elif "amount" in found:
        suggestion.update(amount_column=found["amount"])
        if "drcr" in found:
            suggestion.update(amount_mode="drcr_column", drcr_column=found["drcr"])
        else:
            suggestion.update(amount_mode="signed", signed_positive_means="credit")
    for key in ("reference", "balance"):
        if key in found:
            suggestion[f"{key}_column"] = found[key]
    return suggestion


@dataclass(slots=True)
class NormalizedRow:
    row_index: int
    source_row_number: int
    raw: dict[str, str]
    transaction_date: date | None = None
    value_date: date | None = None
    amount: Decimal | None = None
    currency: str = "INR"
    description: str = ""
    reference: str | None = None
    balance_after: Decimal | None = None
    source_drcr: str | None = None
    channel: PaymentChannel = PaymentChannel.OTHER
    errors: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def valid(self) -> bool:
        return not self.errors and not self.skipped


def _amount(text: str) -> tuple[Decimal, str | None] | None:
    return parse_amount(text)


def normalize_table(table: RawTable, mapping: ColumnMapping) -> list[NormalizedRow]:
    idx = {h: i for i, h in enumerate(table.headers)}
    missing = [c for c in mapping.referenced_columns() if c not in idx]
    if missing:
        raise ValueError(f"mapped columns not present in file: {', '.join(missing)}")
    skip_patterns = [s.lower() for s in mapping.skip_rows_matching]
    out: list[NormalizedRow] = []

    def cell(row: list[str], col: str | None) -> str:
        return row[idx[col]].strip() if col else ""

    for i, (row, line_no) in enumerate(zip(table.rows, table.row_numbers, strict=True)):
        raw = {h: row[j] for j, h in enumerate(table.headers) if row[j]}
        nr = NormalizedRow(row_index=i, source_row_number=line_no, raw=raw, currency=mapping.currency)
        desc = clean_description(" ".join(cell(row, c) for c in mapping.description_columns if cell(row, c)))
        nr.description = desc
        date_text = cell(row, mapping.date_column)
        if (
            date_text
            and not any(ch.isdigit() for ch in date_text)
            and (
                nr_amount_cells_blank(row, cell, mapping) or any(date_text.lower().startswith(p) for p in skip_patterns)
            )
        ):
            nr.skipped = True  # a note/footnote/total label sitting in the date column
            out.append(nr)
            continue
        if not date_text:
            lowered = " ".join(row).lower()
            if any(p in lowered for p in skip_patterns) or not desc:
                nr.skipped = True  # statement summary line or row without transaction content
            else:
                nr.errors.append("missing_date")
            out.append(nr)
            continue
        if any(desc.lower().startswith(p) for p in skip_patterns):
            nr.skipped = True
            out.append(nr)
            continue
        nr.transaction_date = parse_date(mapping.date_format, date_text)
        if nr.transaction_date is None:
            nr.errors.append("invalid_date")
        if mapping.value_date_column:
            vd = cell(row, mapping.value_date_column)
            nr.value_date = parse_date(mapping.date_format, vd) if vd else None
            if vd and nr.value_date is None:
                nr.errors.append("invalid_value_date")
        try:
            nr.amount, nr.source_drcr = _resolve_amount(row, cell, mapping)
            if nr.amount is not None:
                validate_precision(nr.amount, mapping.currency)
                if nr.amount == 0:
                    nr.errors.append("zero_amount")
        except MoneyParseError:
            nr.errors.append("invalid_amount")
        except _AmountError as exc:
            nr.errors.append(str(exc))
        if (
            nr.amount is None
            and "invalid_amount" not in nr.errors
            and not any(e.startswith("amount") for e in nr.errors)
        ):
            nr.errors.append("missing_amount")
        if mapping.balance_column:
            try:
                parsed = parse_amount(cell(row, mapping.balance_column))
                if parsed is not None:
                    bal, marker = parsed
                    nr.balance_after = -abs(bal) if marker == "DR" else bal
            except MoneyParseError:
                nr.errors.append("invalid_balance")
        ref = cell(row, mapping.reference_column) if mapping.reference_column else ""
        nr.reference = normalize_reference(ref) or normalize_reference(extract_reference(desc))
        nr.channel = detect_channel(desc)
        if not desc:
            nr.errors.append("missing_description")
        out.append(nr)
    return out


def nr_amount_cells_blank(row: list[str], cell, mapping: ColumnMapping) -> bool:  # type: ignore[no-untyped-def]
    cols = [mapping.debit_column, mapping.credit_column, mapping.amount_column]
    return not any(cell(row, c) for c in cols if c)


class _AmountError(ValueError):
    pass


def _resolve_amount(row: list[str], cell, mapping: ColumnMapping) -> tuple[Decimal | None, str | None]:  # type: ignore[no-untyped-def]
    if mapping.amount_mode == "split":
        d = parse_amount(cell(row, mapping.debit_column))
        c = parse_amount(cell(row, mapping.credit_column))
        debit = abs(d[0]) if d else None
        credit = abs(c[0]) if c else None
        if debit and credit:
            raise _AmountError("amount_both_debit_and_credit")
        if debit:
            return -debit, "DR"
        if credit:
            return credit, "CR"
        if d is not None or c is not None:
            return Decimal(0), None
        return None, None
    parsed = parse_amount(cell(row, mapping.amount_column))
    if parsed is None:
        return None, None
    value, marker = parsed
    if mapping.amount_mode == "drcr_column":
        flag = cell(row, mapping.drcr_column).strip().upper()
        if flag.startswith(("DR", "D", "DEBIT", "W")):
            return -abs(value), "DR"
        if flag.startswith(("CR", "C", "CREDIT", "DEP")):
            return abs(value), "CR"
        raise _AmountError("amount_unknown_drcr_flag")
    if marker == "DR":
        return -abs(value), "DR"
    if marker == "CR":
        return abs(value), "CR"
    signed = value if mapping.signed_positive_means == "credit" else -value
    return signed, ("CR" if signed > 0 else "DR")
