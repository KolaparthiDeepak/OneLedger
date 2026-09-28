"""Format detection from file content (magic bytes), never from filename or MIME header."""

from __future__ import annotations

import io
import zipfile
from enum import StrEnum


class FileFormat(StrEnum):
    CSV = "CSV"
    XLSX = "XLSX"
    XLS = "XLS"
    PDF = "PDF"


class UnsupportedFile(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


MAX_XLSX_UNCOMPRESSED = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_COMPRESSION_RATIO = 200


def detect_format(data: bytes) -> FileFormat:
    if not data:
        raise UnsupportedFile("EMPTY_FILE", "The file is empty.")
    if data.startswith(b"%PDF-"):
        return FileFormat.PDF
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return FileFormat.XLS  # legacy Excel (e.g. HDFC net-banking download); validated by the parser
    if data.startswith(b"PK\x03\x04"):
        check_xlsx_container(data)
        return FileFormat.XLSX
    if b"\x00" in data[:8192]:
        raise UnsupportedFile("BINARY_FILE", "Unrecognised binary file. Upload CSV, XLSX or a text PDF.")
    try:
        data[:65536].decode("utf-8")
    except UnicodeDecodeError:
        try:
            data[:65536].decode("cp1252")
        except UnicodeDecodeError as exc:
            raise UnsupportedFile("UNKNOWN_ENCODING", "Could not decode the text file.") from exc
    return FileFormat.CSV


def check_xlsx_container(data: bytes) -> None:
    """Reject ZIP bombs, macro-enabled workbooks and external links before parsing."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsupportedFile("CORRUPT_ARCHIVE", "The workbook archive is corrupt.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise UnsupportedFile("ARCHIVE_TOO_LARGE", "The workbook contains too many parts.")
        names = {i.filename for i in infos}
        if "[Content_Types].xml" not in names or not any(n.startswith("xl/") for n in names):
            raise UnsupportedFile("NOT_XLSX", "ZIP archives other than .xlsx workbooks are not supported.")
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise UnsupportedFile("MACRO_WORKBOOK", "Macro-enabled workbooks are not accepted.")
        if any(n.startswith("xl/externalLinks/") for n in names):
            raise UnsupportedFile("EXTERNAL_LINKS", "Workbooks with external links are not accepted.")
        total = 0
        for info in infos:
            total += info.file_size
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise UnsupportedFile("ARCHIVE_BOMB", "The workbook has a suspicious compression ratio.")
        if total > MAX_XLSX_UNCOMPRESSED:
            raise UnsupportedFile("ARCHIVE_TOO_LARGE", "The workbook is too large when uncompressed.")
