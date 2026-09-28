"""Bounded text-PDF statement templates, both verified against the running balance.

``single-line``: each transaction line starts with a date and ends with the amount(s) followed
by the running balance. Direction is derived from the balance change.

``multi-line``: a row starts with a date and its description continues over several lines; the
row ends with a line of three columns ``debit credit balance`` where an empty column is ``-``
(e.g. ``INR 650.00  -  INR 9,350.00``). Debit/credit come from their columns and must match the
balance change; a closing/ending balance, when printed, must equal the last row's balance.

Both need an "Opening Balance" figure. If any row breaks the chain the whole file is declined --
no guessed ledger data is ever produced.

Encrypted/password-protected, scanned and unknown layouts are declined with a clear code.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal

from oneledger_domain.money import MoneyParseError, parse_amount

from .detect import UnsupportedFile
from .mapping import detect_date_formats
from .tabular import RawTable

PdfRow = tuple[int, str, str, str, str, Decimal]  # (line, date, description, debit, credit, balance)
PDF_PARSER_VERSION = "pdf-balance-verified-v2"
MAX_PAGES = 200
MAX_TEXT_CHARS = 5_000_000

_DATE_AT_START = re.compile(
    r"^(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}[\- /][A-Za-z]{3}[\- /]\d{2,4}|\d{4}-\d{2}-\d{2})\s+(.*)$"
)
_AMOUNT_TOKEN = r"\(?-?[\d,]+\.\d{2}\)?(?:\s?(?:Cr|CR|Dr|DR))?"  # noqa: S105 - regex, not a secret
_TRAILING_AMOUNTS = re.compile(rf"^(.*?)\s+((?:{_AMOUNT_TOKEN}\s+){{0,2}}{_AMOUNT_TOKEN})\s*$")
_SLOT = r"(?:INR\s*|Rs\.?\s*|₹\s*)?[\d,]+\.\d{2}(?:\s?(?:Cr|CR|Dr|DR))?"
_SLOT_OR_DASH = rf"(?:{_SLOT}|-)"
_ROW_END = re.compile(rf"^(.*?)\s*(?<![\w/])({_SLOT_OR_DASH})\s+({_SLOT_OR_DASH})\s+({_SLOT})\s*$")
_CLOSING = re.compile(rf"(?:ending|closing)\s+balance[^\d(\-]*({_SLOT})", re.IGNORECASE)
_NOISE = re.compile(r"(?i)^(page\s+\d+(\s+of\s+\d+)?|date\s+(transaction|description|narration|particulars).*balance)$")
_STOP = re.compile(r"(?i)^(ending|closing)\s+balance\b|^total\b")
_OPENING = re.compile(rf"opening\s+balance[^\d(\-]*({_AMOUNT_TOKEN})", re.IGNORECASE)


def extract_text(data: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except (PdfReadError, ValueError, OSError) as exc:
        raise UnsupportedFile("PDF_UNREADABLE", "The PDF could not be read.") from exc
    if reader.is_encrypted:
        raise UnsupportedFile(
            "PDF_ENCRYPTED",
            "Password-protected PDFs are not supported. Export the statement as CSV/XLSX, or save an "
            "unprotected copy. Statement passwords are never stored.",
        )
    if len(reader.pages) > MAX_PAGES:
        raise UnsupportedFile("PDF_TOO_MANY_PAGES", f"PDFs are limited to {MAX_PAGES} pages.")
    lines: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf raises a variety of errors on hostile content
            raise UnsupportedFile("PDF_UNREADABLE", "The PDF text could not be extracted.") from exc
        total += len(text)
        if total > MAX_TEXT_CHARS:
            raise UnsupportedFile("PDF_TOO_LARGE", "The PDF contains too much text.")
        lines.extend(line.strip() for line in text.splitlines() if line.strip())
    if not lines:
        raise UnsupportedFile("PDF_NO_TEXT", "No text found. Scanned PDFs are not supported (no OCR).")
    return lines


def _money(tok: str) -> tuple[Decimal, str | None]:
    parsed = parse_amount(tok)
    if parsed is None:
        raise MoneyParseError("blank")
    return parsed


def _date_formats_for(values: list[str]) -> tuple[list[str], bool]:
    formats, ambiguous = detect_date_formats(values)
    if not formats:
        raise UnsupportedFile("PDF_UNSUPPORTED_LAYOUT", "Unrecognised date format in PDF.")
    return formats, ambiguous


def _signed(tok: str) -> Decimal:
    value, marker = _money(tok)
    return -abs(value) if marker == "DR" else value


def _opening_balance(lines: list[str]) -> Decimal:
    for line in lines:
        m = _OPENING.search(line)
        if m:
            return _signed(m.group(1))
    raise UnsupportedFile(
        "PDF_UNSUPPORTED_LAYOUT",
        "This PDF layout is not supported (no opening balance found). Export CSV/XLSX instead.",
    )


def _single_line_rows(lines: list[str], opening: Decimal) -> list[PdfRow]:
    candidates: list[tuple[int, str, str, list[str]]] = []
    for n, line in enumerate(lines, start=1):
        dm = _DATE_AT_START.match(line)
        if not dm:
            continue
        am = _TRAILING_AMOUNTS.match(dm.group(2))
        if not am:
            continue
        tokens = re.findall(_AMOUNT_TOKEN, am.group(2))
        if len(tokens) < 2:
            continue
        candidates.append((n, dm.group(1), am.group(1).strip(), tokens))
    rows: list[PdfRow] = []
    prev = opening
    for n, date_text, desc, tokens in candidates:
        try:
            balance = _signed(tokens[-1])
            amounts = [abs(_money(t)[0]) for t in tokens[:-1]]
        except MoneyParseError as exc:
            raise UnsupportedFile("PDF_UNSUPPORTED_LAYOUT", "Unparseable amount in PDF.") from exc
        delta = balance - prev
        nonzero = [a for a in amounts if a != 0]
        if len(nonzero) != 1 or abs(delta) != nonzero[0]:
            raise UnsupportedFile(
                "PDF_BALANCE_CHAIN_BROKEN",
                f"Running balance does not reconcile at PDF line {n}; the file was declined to avoid guessing.",
            )
        debit, credit = (f"{-delta:.2f}", "") if delta < 0 else ("", f"{delta:.2f}")
        rows.append((n, date_text, desc, debit, credit, balance))
        prev = balance
    return rows


def _multi_line_rows(lines: list[str], opening: Decimal) -> list[PdfRow]:
    rows: list[PdfRow] = []
    prev = opening
    current: tuple[int, str, list[str]] | None = None
    closing: Decimal | None = None
    for n, line in enumerate(lines, start=1):
        if _STOP.match(line):
            m = _CLOSING.search(line)
            if m:
                closing = _signed(m.group(1))
            if rows or current:
                if current is not None:
                    raise UnsupportedFile(
                        "PDF_UNSUPPORTED_LAYOUT", f"Transaction at PDF line {current[0]} has no amounts."
                    )
                break
            continue
        if _NOISE.match(line):
            continue
        dm = _DATE_AT_START.match(line)
        if dm:
            if current is not None:
                raise UnsupportedFile(
                    "PDF_UNSUPPORTED_LAYOUT", f"Transaction at PDF line {current[0]} has no amounts; declined."
                )
            current = (n, dm.group(1), [])
            line = dm.group(2)
        if current is None:
            continue  # page headers, account details, summary block
        end = _ROW_END.match(line)
        if end is None:
            if line:
                current[2].append(line)
            continue
        if end.group(1):
            current[2].append(end.group(1))
        try:
            debit = Decimal(0) if end.group(2) == "-" else abs(_money(end.group(2))[0])
            credit = Decimal(0) if end.group(3) == "-" else abs(_money(end.group(3))[0])
            balance = _signed(end.group(4))
        except MoneyParseError as exc:
            raise UnsupportedFile("PDF_UNSUPPORTED_LAYOUT", "Unparseable amount in PDF.") from exc
        if (debit == 0) == (credit == 0) or balance - prev != credit - debit:
            raise UnsupportedFile(
                "PDF_BALANCE_CHAIN_BROKEN",
                f"Running balance does not reconcile at PDF line {n}; the file was declined to avoid guessing.",
            )
        desc = re.sub(r"\s+", " ", " ".join(current[2])).strip()
        rows.append(
            (current[0], current[1], desc, f"{debit:.2f}" if debit else "", f"{credit:.2f}" if credit else "", balance)
        )
        prev = balance
        current = None
    if current is not None:
        raise UnsupportedFile("PDF_UNSUPPORTED_LAYOUT", f"Transaction at PDF line {current[0]} has no amounts.")
    if rows and closing is not None and closing != prev:
        raise UnsupportedFile(
            "PDF_BALANCE_CHAIN_BROKEN", "The statement's ending balance does not match its transactions; declined."
        )
    return rows


def parse_pdf(data: bytes) -> RawTable:
    lines = extract_text(data)
    opening = _opening_balance(lines)
    rows = _single_line_rows(lines, opening)
    template = "single-line"
    if not rows:
        rows = _multi_line_rows(lines, opening)
        template = "multi-line"
    if not rows:
        raise UnsupportedFile(
            "PDF_UNSUPPORTED_LAYOUT",
            "No transaction lines matched a supported PDF layout. Export the statement as CSV or Excel instead.",
        )
    formats, ambiguous = _date_formats_for([r[1] for r in rows])
    table = RawTable(
        headers=["Date", "Description", "Debit", "Credit", "Balance"],
        rows=[[date, desc, debit, credit, f"{bal:.2f}"] for _n, date, desc, debit, credit, bal in rows],
        row_numbers=[r[0] for r in rows],
        header_row_number=0,
        parser="pdf",
        parser_version=PDF_PARSER_VERSION,
    )
    table.notes.append(f"template={template}")
    # Day/month order is confirmed by the user in the mapping step when ambiguous.
    table.notes.append(f"date_formats={','.join(formats)}")
    table.notes.append(f"date_ambiguous={ambiguous}")
    return table
