from __future__ import annotations

import io
import zipfile
from decimal import Decimal as D

import pytest
from oneledger_providers.imports import (
    ColumnMapping,
    UnsupportedFile,
    detect_date_formats,
    detect_format,
    match_preset,
    normalize_table,
    parse_csv,
    parse_pdf,
    parse_xls,
    parse_xlsx,
    preset_mapping,
    suggest_mapping,
)

BANK = b"""HDFC BANK LTD
Statement for account XXXX1234
Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/08/26,SALARY ACME,SAL01,01/08/26,,"1,00,000.00","1,00,000.00"
03/08/26,UPI-SWIGGY-612345678901,612345678901,03/08/26,650.00,,"99,350.00"
,Opening balance,,,,,
"""


def test_csv_header_detection_and_suggestion():
    t = parse_csv(BANK)
    assert t.headers[0] == "Date" and len(t.rows) == 3
    s = suggest_mapping(t)
    assert s["amount_mode"] == "split"
    assert s["date_format_ambiguous"] and "date_format" not in s  # 01/08/26 could be Jan 8: user must choose
    m = ColumnMapping.model_validate(
        {**{k: v for k, v in s.items() if k in ColumnMapping.model_fields}, "date_format": "DD/MM/YY"}
    )
    rows = normalize_table(t, m)
    assert rows[0].amount == D("100000.00") and rows[1].amount == D("-650.00")
    assert rows[1].reference == "612345678901"
    assert rows[2].skipped


def test_ambiguous_dates_require_choice():
    formats, ambiguous = detect_date_formats(["03/04/2026", "05/06/2026"])
    assert ambiguous and {"DD/MM/YYYY", "MM/DD/YYYY"} <= set(formats)
    formats, ambiguous = detect_date_formats(["03/04/2026", "25/06/2026"])
    assert not ambiguous and formats == ["DD/MM/YYYY"]


def test_magic_bytes_not_filename():
    fake_xls = b"\xd0\xcf\x11\xe0" + b"\x00" * 100
    assert detect_format(fake_xls).value == "XLS"
    with pytest.raises(UnsupportedFile) as e:
        parse_xls(fake_xls)
    assert e.value.code == "XLS_UNREADABLE"
    with pytest.raises(UnsupportedFile):
        detect_format(b"\x00\x01\x02binary")


def _xlsx(rows, extra: dict[str, bytes] | None = None) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    if not extra:
        return buf.getvalue()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for i in src.infolist():
            dst.writestr(i, src.read(i))
        for name, data in extra.items():
            dst.writestr(name, data)
    return out.getvalue()


def test_xlsx_parses_and_formulas_not_evaluated():
    data = _xlsx(
        [
            ["Date", "Description", "Debit", "Credit"],
            ["01/08/2026", "SHOP", 100.01, None],
            ["02/08/2026", '=HYPERLINK("http://evil")', None, "50"],
        ]
    )
    t = parse_xlsx(data)
    assert t.rows[0][2] == "100.01"
    assert t.rows[1][1] == ""  # formula without cached value is never evaluated


def test_f21_xlsx_macro_and_bomb_rejected():
    with pytest.raises(UnsupportedFile) as e:
        detect_format(_xlsx([["a"]], {"xl/vbaProject.bin": b"x"}))
    assert e.value.code == "MACRO_WORKBOOK"
    with pytest.raises(UnsupportedFile) as e:
        detect_format(_xlsx([["a"]], {"xl/bomb.xml": b"0" * 50_000_000}))
    assert e.value.code in ("ARCHIVE_BOMB", "ARCHIVE_TOO_LARGE")


def _pdf(lines: list[str]) -> bytes:
    """Minimal single-page text PDF built by hand (no extra dependency)."""
    y = 780
    ops = ["BT /F1 9 Tf"]
    for line in lines:
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 40 {y} Tm ({esc}) Tj")
        y -= 14
    ops.append("ET")
    stream = "\n".join(ops).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref))
    return out.getvalue()


def test_pdf_balance_verified_template():
    t = parse_pdf(
        _pdf(
            [
                "Opening Balance 10,000.00",
                "01/08/2026 SALARY ACME 5,000.00 15,000.00",
                "02/08/2026 SWIGGY ORDER 650.00 14,350.00",
            ]
        )
    )
    assert t.rows[0][3] == "5000.00" and t.rows[1][2] == "650.00"
    assert "date_ambiguous=True" in t.notes  # 01/08 vs 08/01: the user confirms in the mapping step


def test_pdf_broken_chain_is_declined_not_guessed():
    with pytest.raises(UnsupportedFile) as e:
        parse_pdf(_pdf(["Opening Balance 10,000.00", "01/08/2026 SALARY 5,000.00 99,000.00"]))
    assert e.value.code == "PDF_BALANCE_CHAIN_BROKEN"


def test_pdf_without_template_declined():
    with pytest.raises(UnsupportedFile):
        parse_pdf(_pdf(["Hello world", "Nothing to see"]))


MULTI = [
    "ACCOUNT STATEMENT",
    "Opening Balance INR 10,000.00",
    "Total Credits + INR 5,000.00",
    "Total Debits - INR 650.00",
    "Ending Balance INR 14,350.00",
    "Date Transaction Details Debits Credits Balance",
    "01 Aug 2026 UPI/DR/612345678901/SWIGGY",
    "/YESB/swiggy@ybl/Food",
    "INR 650.00  -  INR 9,350.00",
    "02 Aug 2026 NEFT CR-ACME TECHNOLOGIES",
    "SALARY AUG",
    "-  INR 5,000.00  INR 14,350.00",
    "Page 1 of 1",
    "Ending Balance INR 14,350.00",
]


def test_pdf_multi_line_rows_with_debit_credit_columns():
    t = parse_pdf(_pdf(MULTI))
    assert "template=multi-line" in t.notes
    assert t.rows[0] == ["01 Aug 2026", "UPI/DR/612345678901/SWIGGY /YESB/swiggy@ybl/Food", "650.00", "", "9350.00"]
    assert t.rows[1] == ["02 Aug 2026", "NEFT CR-ACME TECHNOLOGIES SALARY AUG", "", "5000.00", "14350.00"]
    assert "date_ambiguous=False" in t.notes


def test_pdf_multi_line_broken_chain_declined():
    bad = [line.replace("INR 9,350.00", "INR 9,300.00") for line in MULTI]
    with pytest.raises(UnsupportedFile) as e:
        parse_pdf(_pdf(bad))
    assert e.value.code == "PDF_BALANCE_CHAIN_BROKEN"


def test_pdf_multi_line_ending_balance_must_match():
    bad = [*MULTI[:-1], "Ending Balance INR 99,999.00"]
    with pytest.raises(UnsupportedFile) as e:
        parse_pdf(_pdf(bad))
    assert e.value.code == "PDF_BALANCE_CHAIN_BROKEN"


HDFC_HEADERS = ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]


def _hdfc_xls() -> bytes:
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet 1")
    rows = [
        ["HDFC BANK Ltd."],
        ["Statement of account"],
        HDFC_HEADERS,
        ["01/08/26", "SALARY ACME", "SAL01", "01/08/26", "", 100000.0, 100000.0],
        ["03/08/26", "UPI-SWIGGY-612345678901", "0000612345678901", "03/08/26", 650.0, "", 99350.0],
    ]
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xls_hdfc_export_parses_and_matches_preset():
    data = _hdfc_xls()
    assert detect_format(data).value == "XLS"
    t = parse_xls(data)
    assert t.headers == HDFC_HEADERS
    preset = match_preset(t.headers)
    assert preset is not None and preset.key == "hdfc-netbanking"
    m = ColumnMapping.model_validate(preset_mapping(preset, t.headers, "INR"))
    rows = normalize_table(t, m)
    assert [r.amount for r in rows] == [D("100000"), D("-650")]
    assert rows[0].transaction_date.isoformat() == "2026-08-01"  # 01/08/26 is 1 Aug, not 8 Jan


def test_preset_tolerates_header_padding_and_case():
    padded = [
        "Date     ",
        "Narration",
        "Chq./Ref.No.",
        "Value Dt",
        "Withdrawal Amt.",
        "Deposit Amt.",
        "Closing Balance",
    ]
    assert match_preset(padded) is not None
    assert match_preset(["Date", "Details", "Amount"]) is None


def test_xlsx_real_dates_accepted_whatever_format_and_footnotes_skipped():
    from datetime import datetime as dt

    data = _xlsx(
        [
            ["Txn Date", "Description", "Debit", "Credit", "Balance"],
            [
                dt(2026, 7, 14),
                "RATN000RAPL/CITY CABS PVT LTD/XXXXX /citycabs@rapl/UPI/612300000002/Cab",
                123.45,
                None,
                1000.0,
            ],
            [dt(2026, 7, 15), "SALARY", None, 5000.0, 6000.0],
            ["Total", None, 123.45, 5000.0, None],
            [
                "NEFT: National Electronic Funds Transfer. This is a computer-generated statement.",
                None,
                None,
                None,
                None,
            ],
        ]
    )
    t = parse_xlsx(data)
    s = suggest_mapping(t)
    assert s["date_format"] == "YYYY-MM-DD" and not s["date_format_ambiguous"]
    # Even if the user picks DD/MM/YYYY (how Excel displays it), real dates still parse.
    m = ColumnMapping(
        date_column="Txn Date",
        date_format="DD/MM/YYYY",
        description_columns=["Description"],
        amount_mode="split",
        debit_column="Debit",
        credit_column="Credit",
        balance_column="Balance",
    )
    rows = normalize_table(t, m)
    assert [r.transaction_date.isoformat() for r in rows[:2]] == ["2026-07-14", "2026-07-15"]
    assert rows[0].amount == D("-123.45") and not rows[0].errors
    assert rows[2].skipped and rows[3].skipped and not rows[2].errors and not rows[3].errors


def test_card_statement_drcr_column_is_detected_not_mistaken_for_debit():
    t = parse_csv(
        b"Date,Transaction Details,Amount,Dr/Cr\n04/06/2026,FLIPKART,972.25,Dr\n22/06/2026,PAYMENT RECEIVED,15000.00,Cr\n"
    )
    s = suggest_mapping(t)
    assert s["amount_mode"] == "drcr_column" and s["drcr_column"] == "Dr/Cr" and s["amount_column"] == "Amount"
    assert "debit_column" not in s and "credit_column" not in s
    m = ColumnMapping.model_validate(
        {**{k: v for k, v in s.items() if k in ColumnMapping.model_fields}, "date_format": "DD/MM/YYYY"}
    )
    rows = normalize_table(t, m)
    assert [r.amount for r in rows] == [D("-972.25"), D("15000.00")]


def test_suggestion_recognises_less_common_headers():
    t = parse_csv(b"Txn Posted,Particulars,Debit INR,Credit INR,Running Bal\n03/04/26,UPI-ZOMATO,250.00,,9750.00\n")
    s = suggest_mapping(t)
    assert s["date_column"] == "Txn Posted" and s["description_columns"] == ["Particulars"]
    assert (
        s["debit_column"] == "Debit INR" and s["credit_column"] == "Credit INR" and s["balance_column"] == "Running Bal"
    )
