"""Parse statement files into a bounded raw table of strings.

Parsers preserve original cell text and row location. No values are interpreted here.
"""

from __future__ import annotations

import csv
import io
import re
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime

from .detect import UnsupportedFile, check_xlsx_container

MAX_ROWS = 100_000
MAX_COLUMNS = 60
MAX_CELL_CHARS = 2000
HEADER_SCAN_ROWS = 40

_HEADER_WORDS = re.compile(
    r"\b(date|txn|transaction|value|narration|description|particulars|details|remarks|withdrawal|deposit|"
    r"debit|credit|amount|balance|dr|cr|reference|ref|chq|cheque|utr)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RawTable:
    headers: list[str]
    rows: list[list[str]]
    row_numbers: list[int]  # 1-based source line/row numbers for each data row
    header_row_number: int
    sheet: str | None = None
    parser: str = ""
    parser_version: str = ""
    notes: list[str] = field(default_factory=list)
    # Text above the transaction table (bank name, "Statement of account XXXX1234"). Used only to
    # suggest which account the file belongs to; never stored.
    preamble: list[str] = field(default_factory=list)


def _clip(v: str) -> str:
    return v[:MAX_CELL_CHARS]


def _header_index(rows: list[list[str]]) -> int:
    """First row within the scan window that looks like a statement header."""
    best, best_score = 0, 0
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        score = sum(1 for cell in row if cell and _HEADER_WORDS.search(cell) and len(cell) < 60)
        if score >= 2 and score > best_score:
            best, best_score = i, score
            if score >= 3:
                break
    return best


def _finish(grid: list[tuple[int, list[str]]], parser: str, version: str, sheet: str | None = None) -> RawTable:
    grid = [(n, r) for n, r in grid if any(c.strip() for c in r)]
    if not grid:
        raise UnsupportedFile("NO_ROWS", "No rows were found in the file.")
    idx = _header_index([r for _, r in grid])
    header_no, header = grid[idx]
    width = min(max(len(header), *(len(r) for _, r in grid[idx + 1 :]), 1), MAX_COLUMNS)
    headers = [(header[i].strip() if i < len(header) else "") or f"Column {i + 1}" for i in range(width)]
    # De-duplicate header names while preserving order.
    seen: dict[str, int] = {}
    for i, h in enumerate(headers):
        if h in seen:
            seen[h] += 1
            headers[i] = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
    data = grid[idx + 1 :]
    if len(data) > MAX_ROWS:
        raise UnsupportedFile("TOO_MANY_ROWS", f"The file has more than {MAX_ROWS} rows.")
    rows = [[_clip(r[i].strip()) if i < len(r) else "" for i in range(width)] for _, r in data]
    preamble = [" ".join(c.strip() for c in r if c.strip())[:MAX_CELL_CHARS] for _, r in grid[:idx]]
    return RawTable(headers, rows, [n for n, _ in data], header_no, sheet, parser, version, preamble=preamble)


CSV_PARSER_VERSION = "csv-v1"


def parse_csv(data: bytes, delimiter: str | None = None, encoding: str | None = None) -> RawTable:
    text: str | None = None
    for enc in [encoding] if encoding else ["utf-8-sig", "cp1252"]:
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise UnsupportedFile("UNKNOWN_ENCODING", "Could not decode the CSV file.")
    if delimiter is None:
        sample = "\n".join(text.splitlines()[:50])
        counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
        delimiter = max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","
    if delimiter not in (",", ";", "\t", "|"):
        raise UnsupportedFile("BAD_DELIMITER", "Unsupported delimiter.")
    csv.field_size_limit(MAX_CELL_CHARS * 4)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    grid: list[tuple[int, list[str]]] = []
    try:
        for i, row in enumerate(reader, start=1):
            if i > MAX_ROWS + HEADER_SCAN_ROWS:
                raise UnsupportedFile("TOO_MANY_ROWS", f"The file has more than {MAX_ROWS} rows.")
            grid.append((i, row[:MAX_COLUMNS]))
    except csv.Error as exc:
        raise UnsupportedFile("CSV_MALFORMED", "The CSV file is malformed.") from exc
    table = _finish(grid, "csv", CSV_PARSER_VERSION)
    table.notes.append(f"delimiter={delimiter!r}")
    return table


XLSX_PARSER_VERSION = "xlsx-v1"


def _cell_text(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        # Spreadsheet numerics arrive as binary floats; render with fixed decimals and let the
        # Decimal parser validate. repr() avoids scientific notation for statement magnitudes.
        text = f"{v:.10f}".rstrip("0").rstrip(".")
        return text
    return str(v)


def parse_xlsx(data: bytes, sheet_name: str | None = None) -> RawTable:
    from openpyxl import load_workbook

    check_xlsx_container(data)
    try:
        with warnings.catch_warnings():  # bank exports often lack a default style; harmless
            warnings.simplefilter("ignore", UserWarning)
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise UnsupportedFile("XLSX_UNREADABLE", "The workbook could not be read.") from exc
    try:
        ws = wb[sheet_name] if sheet_name else wb.worksheets[0]
        grid: list[tuple[int, list[str]]] = []
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if i > MAX_ROWS + HEADER_SCAN_ROWS:
                raise UnsupportedFile("TOO_MANY_ROWS", f"The workbook has more than {MAX_ROWS} rows.")
            grid.append((i, [_cell_text(v) for v in row[:MAX_COLUMNS]]))
        title = ws.title
    finally:
        wb.close()
    return _finish(grid, "xlsx", XLSX_PARSER_VERSION, sheet=title)


XLS_PARSER_VERSION = "xls-v1"


def parse_xls(data: bytes, sheet_index: int = 0) -> RawTable:
    """Legacy .xls via xlrd (values only; formulas are never evaluated)."""
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=False)
    except Exception as exc:  # xlrd raises several error types on malformed or non-workbook OLE files
        raise UnsupportedFile("XLS_UNREADABLE", "The .xls file could not be read. Save it as .xlsx or CSV.") from exc
    try:
        if book.nsheets < 1:
            raise UnsupportedFile("NO_ROWS", "The workbook has no sheets.")
        sheet = book.sheet_by_index(sheet_index)
        if sheet.nrows > MAX_ROWS + HEADER_SCAN_ROWS:
            raise UnsupportedFile("TOO_MANY_ROWS", f"The workbook has more than {MAX_ROWS} rows.")
        grid: list[tuple[int, list[str]]] = []
        for r in range(sheet.nrows):
            cells = []
            for c in range(min(sheet.ncols, MAX_COLUMNS)):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    cells.append(_cell_text(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)))
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    cells.append(_cell_text(float(cell.value)))
                else:
                    cells.append(str(cell.value))
            grid.append((r + 1, cells))
        title = sheet.name
    finally:
        book.release_resources()
    return _finish(grid, "xls", XLS_PARSER_VERSION, sheet=title)
