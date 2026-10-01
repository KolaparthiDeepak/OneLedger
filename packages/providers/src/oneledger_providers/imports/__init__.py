"""Statement import pipeline: detect -> parse -> map -> normalize."""

from __future__ import annotations

from .account_detect import AccountHint, Suggestion, suggest_account
from .detect import FileFormat, UnsupportedFile, detect_format
from .mapping import ColumnMapping, NormalizedRow, detect_date_formats, normalize_table, suggest_mapping
from .pdf_parser import PDF_PARSER_VERSION, parse_pdf
from .presets import PRESETS, BankPreset, match_preset, preset_mapping
from .tabular import (
    CSV_PARSER_VERSION,
    XLS_PARSER_VERSION,
    XLSX_PARSER_VERSION,
    RawTable,
    parse_csv,
    parse_xls,
    parse_xlsx,
)


def parse_file(data: bytes, fmt: FileFormat) -> RawTable:
    if fmt == FileFormat.CSV:
        return parse_csv(data)
    if fmt == FileFormat.XLSX:
        return parse_xlsx(data)
    if fmt == FileFormat.XLS:
        return parse_xls(data)
    return parse_pdf(data)


def parser_version(fmt: FileFormat) -> str:
    return {
        FileFormat.CSV: CSV_PARSER_VERSION,
        FileFormat.XLSX: XLSX_PARSER_VERSION,
        FileFormat.XLS: XLS_PARSER_VERSION,
        FileFormat.PDF: PDF_PARSER_VERSION,
    }[fmt]


__all__ = [
    "PRESETS",
    "AccountHint",
    "BankPreset",
    "ColumnMapping",
    "FileFormat",
    "NormalizedRow",
    "RawTable",
    "Suggestion",
    "UnsupportedFile",
    "detect_date_formats",
    "detect_format",
    "match_preset",
    "normalize_table",
    "parse_csv",
    "parse_file",
    "parse_pdf",
    "parse_xls",
    "parse_xlsx",
    "parser_version",
    "preset_mapping",
    "suggest_account",
    "suggest_mapping",
]
