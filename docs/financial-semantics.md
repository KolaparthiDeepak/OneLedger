# Financial semantics

These rules are implemented in `packages/finance-domain` and enforced by database constraints.
Every report, the web app, the MCP tools and the AI assistant use the same definitions.

## Money, signs and dates

- Amounts are `NUMERIC(24,8)` in the database, `Decimal` in Python and **decimal strings** in JSON
  (`"-1250.00"`). JSON numbers are rejected for money inputs.
- A transaction's signed amount is its effect on your equity: money into a bank account `+`,
  money out `-`; a card purchase `-`, a card payment received `+`; loan principal repaid on the
  loan account `+`. Asset balances change by `amount`; amounts owed change by `-amount`.
- Date ranges are half-open `[start_date, end_date_exclusive)`. Months are calendar months in the
  owner's time zone (default Asia/Kolkata). Date-only statements never get an invented time.

## Movements vs meaning

A **transaction** is an account movement. Its **allocations** say what it means; they must sum
exactly to the transaction amount (deferred database trigger). Allocation *effects*:

| Effect | Counted as |
|---|---|
| `income` | Income |
| `expense` | Spending; a positive expense allocation is a refund and reduces spending |
| `transfer` | Nothing (movement between your own accounts, incl. card bill payments) |
| `investment` | Investment contributions/withdrawals, never spending |
| `loan_principal` | Loan principal repaid, never spending |
| `adjustment` | Nothing |
| `unclassified` | Shown separately as "not yet categorised"; totals are marked provisional |

Categories only affect presentation; renaming a category never changes its accounting effect.

## Report definitions

- **Income** = sum of `income` allocations. Excludes transfers, refunds, borrowing, asset sales.
- **Net expenses (Spent)** = −sum of `expense` allocations (purchases minus refunds, on the refund date).
- **Savings** = income − net expenses; provisional while unclassified allocations exist.
- **Cash flow** = gross in/out on bank, cash and wallet accounts, plus "external" flow excluding
  confirmed own-account transfers and loan principal.
- **Bank balance** = latest recorded balance per account at/before the date plus later posted
  movements. An account without any recorded balance is **unknown**, never zero.
- **Net worth** = included asset balances/valuations − included amounts owed, grouped by currency.
  Missing components make it *partial*. 30/90/365-day changes are only shown when the earlier
  point covers exactly the same components.
- Only posted, published, non-deleted, non-merged transactions count. Pending ones are shown
  separately; an import batch is invisible until every accepted row has committed.
- **Deleting an import** soft-deletes every transaction that import added (with an audit record),
  undoes transfers, loan payments (and their derived loan-account movements), merges and review
  items that depended on them, removes the statement balance it recorded, and erases the uploaded
  file. Transactions that were already in the ledger and only *linked* by the file are kept. The
  import stays listed as `DELETED`; the same file can be imported again and is added fresh.

## Required behaviours (known-answer tests)

| Event | Representation | Result |
|---|---|---|
| Salary ₹1,00,000 | bank +100000 income | income 100000 |
| Own transfer ₹50,000 | −50000 / +50000 linked transfer | no income, no spending |
| ₹650 purchase, ₹200 refund | −650 expense, +200 expense | spent 450 |
| Card purchase ₹3,000 + bill paid | card −3000 expense; bank −3000 ↔ card +3000 transfer | spent 3000 once |
| EMI ₹10,000 (8,000 principal) | bank split −8000 loan_principal / −2000 expense; loan +8000 | spent 2000, owed −8000 |
| ATM ₹2,000 then ₹300 cash spend | bank −2000 ↔ cash +2000 transfer; cash −300 | spent 300 |
| Opening balance | balance snapshot | never income |

Tests: `tests/integration/test_financial_scenarios.py`, `tests/unit/test_domain.py`.

## Duplicates

Identity priority: same file re-imported (linked by row) → provider transaction ID → shared bank
reference (UTR/RRN) with same amount within 3 days → fingerprint (account, date, amount,
currency, normalised description, reference) *with multiplicity*. A fingerprint match is only
automatic when the running balance also agrees; otherwise both rows are kept and a review item is
created. Two identical genuine purchases stay two purchases. Duplicates are never merged across
accounts. Merges are reversible.

## Transfers

Candidates: equal and opposite amounts, same currency, different accounts, within 3 days.
Confirmed automatically only when unique **and** backed by a shared reference or a description
naming the other account's last four digits. Amount/date alone creates a suggestion for review.
Your confirm/reject decisions are permanent and never overridden by later automatic runs.

## Categorisation order

Your manual choice (locked) → your rules (by priority) → your merchant mappings → built-in Indian
merchant and keyword patterns (including words in the UPI payee name and note, e.g. "pizza",
"kirana", "medicals") → optional AI → unclassified.

AI has two modes, both off by default: *suggestions* you accept one by one, and *automatic*
categorisation after each import. Automatic mode applies only medium/high-confidence answers,
never assigns transfer, investment, loan-principal or adjustment categories, never labels money
going out as income, and never overrides your own choices. AI-set categories are marked "by AI",
survive rule re-runs unless a rule matches, and can be changed at any time.

## Loans

EMI = P·r·(1+r)ⁿ / ((1+r)ⁿ−1) with r = annual rate/12 (P/n at 0%). Interest is rounded per
period and the final payment clears the balance. Negative amortisation is rejected. Rate changes
keep the EMI (tenure moves) by default. Prepayment simulation (reduce tenure or reduce EMI) never
changes recorded balances. Lender-reported components always override estimates, and estimates
are labelled.
