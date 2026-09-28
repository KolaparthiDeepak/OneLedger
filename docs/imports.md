# Importing statements

OneLedger gets your data only from statements you import and entries you make yourself. There is
no bank connection or Account Aggregator (consent-based bank data sharing) link: bank connections
were removed on 28 Sep 2026 (migration `0005_remove_bank_connections`). Parsers live in
`packages/providers/src/oneledger_providers/imports/`.

## Formats

- **CSV / delimited text**: delimiter and encoding detection, header row detection (bank preambles
  skipped), column mapping with Indian digit grouping, parentheses/Dr/Cr, split debit/credit
  columns. Ambiguous dates (e.g. `03/04/26`) require you to choose the format unless a bank preset
  fixes it.
- **XLSX and legacy XLS**: values only, formulas never evaluated; macro workbooks, external links
  and ZIP bombs are rejected. `.xls` is read with `xlrd`.
- **PDF** (text PDFs only; no OCR, password-protected files declined), two layouts, both verified
  against the running balance so nothing is guessed:
  - *single-line*: `date … amount(s) balance` on one line;
  - *multi-line*: a row starts with a date, the description continues on following lines, and the
    row ends with `debit  credit  balance` where an empty column is `-`
    (e.g. `INR 650.00  -  INR 9,350.00`). A printed "Ending/Closing Balance" must match.
- A mapping you confirm once is reused automatically for the next file with the same columns on
  that account.

### Bank presets (mapped automatically on first import)

| Preset | Matched columns |
|---|---|
| HDFC Bank net banking (XLS/CSV) | Date, Narration, Chq./Ref.No., Value Dt, Withdrawal Amt., Deposit Amt., Closing Balance (dates DD/MM/YY) |
| HDFC Bank delimited export | Date, Narration, Value Dat, Debit Amount, Credit Amount, Chq/Ref Number, Closing Balance |

Presets are added only from real export headers (`packages/providers/src/oneledger_providers/imports/presets.py`).
Union Bank has no CSV/Excel preset yet because its column layout has not been confirmed; its PDF
statements in the multi-line layout above import directly, and a CSV/Excel mapping you confirm
once is remembered.

### Where to download statements

Menu names change between bank app versions; these are the usual paths.

- **HDFC Bank** (NetBanking): Accounts → Account Statement (or "Enquire → A/c Statement") → choose
  the account and date range → format **XLS** or **Delimited**. Use either; both match the preset.
- **Union Bank of India** (net banking or the Vyom app): Accounts → Account Statement → choose
  the period → download as **PDF** or **Excel**. The PDF imports directly. For Excel, map the
  columns once on the first import.
- Prefer a full calendar-month range and keep the opening/closing balance lines: they let
  OneLedger check that nothing is missing.
