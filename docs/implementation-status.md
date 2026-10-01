# Implementation status

Last updated: 1 Oct 2026. Latest migration revision: `0013`.

"Verified" means a test or command in this repository exercised it in this session.
"Implemented" means the code exists but was not exercised end to end. "Unverified" means it
depends on infrastructure that was not available.

## Checks run (all passing)

| Check | Command | Result |
|---|---|---|
| Lint / format | `uv run ruff check . && uv run ruff format --check .` | pass (141 files) |
| Types (Python, strict) | `uv run mypy apps/api/src packages` ; `uv run mypy apps/mcp/src` | pass (99 files) |
| Types (web) | `cd apps/web && npx tsc --noEmit` | pass |
| Backend tests (real PostgreSQL 15, runtime role with RLS) | `uv run pytest tests -q` | 190 passed |
| Migrations | `alembic downgrade base` then `upgrade head` on `oneledger_test` | pass |
| Web production build | `npm run build` | pass (26 routes) |
| Browser e2e (Playwright, Chromium) | `./scripts/e2e.sh` | 21 passed (account picked from a statement file, card billing dates from Add account, phone alerts on/off, first run + import/re-import, BFF checks, card matching, loan EMIs matched automatically + lender figures + prepayment simulation, rules dry-run, budgets/net worth/settings, delete import, tags + bulk categorise, invite a person, quick add with a sum + template, manual transfer, calendar, stats, sharing a bill, receipts, budget suggestions + alerts, password change, phone + button) |
| Backup + restore drill | `scripts/backup.sh`, `scripts/restore.sh` into an isolated DB | pass (66 txns, schema 0002) |
| Key rotation | `tests/security/test_key_rotation.py` | pass |
| Performance, 100,000 transactions, local laptop, warm | curl ×6 per endpoint | transaction page median 8–20 ms; yearly summary 48 ms; dashboard 145 ms (targets 500 ms / 1 s) |

## Phase status

| Task | Status | Evidence |
|---|---|---|
| P0.1 workspace, config validation, locks, lint/type/test, `.env.example`, status doc | Verified | uv workspace + `uv.lock`, `package-lock.json`, checks above |
| P0.2 local stack + health + shell | Verified (without Docker) | `make dev`/local Postgres; `/api/health`, `/api/ready` |
| P0.3 Vercel/Supabase configuration | Implemented, **unverified** | `vercel.json`, `api/index.py`, `infrastructure/deployment/supabase/*.sql`, `docs/deployment.md` |
| P0.4 durable job survives crash / duplicate delivery; scheduled runner | Verified locally | `test_f12_*`, `test_f13_*`, `test_runner_requires_scheduler_secret`; hosted cron unverified |
| P0.5 semantics, RLS design, threat model | Verified | `docs/financial-semantics.md`, `docs/security.md`, RLS tests |
| P1.1 auth, owner allowlist, MFA, private schema, RLS, audit, migrations | Verified | `test_isolation_auth.py`, e2e login |
| P1.2 accounts, opening balances, categories, manual entries, allocations, revisions | Verified | `test_financial_scenarios.py` |
| P1.3 CSV import pipeline, dedup, job status | Verified | F02, F03, F12, e2e import + re-import |
| P1.4 transfers, refunds, reversals, splits, tags, corrections, merge/undo | Verified | F05–F10, `test_merge_and_undo` |
| P1.5 categorization, merchants, rules with dry-run, manual locks | Verified | `test_categorization.py`, `test_manual_lock_survives_rule_application` |
| P1.6 explorer, dashboard, reports, reconciliation, coverage | Verified | e2e screenshots, `test_evidence_query_reproduces_totals` |
| P1.7 XLSX + bounded PDF template, unsupported-file UX | Verified | `tests/providers/test_parsers.py`, F21 |
| P2 bank connections (Account Aggregator, OneMoney, consent, sync) | **Removed** (28 Sep 2026, owner decision) | migration `0005_remove_bank_connections`; data comes from statement imports only |
| P3.1 cards, statements, payment matching | Verified | F07 |
| P3.2 loans, schedules, rate changes, EMI split, prepayment simulation | Verified | F08, F16 |
| P3.3 investments, valuations, no double counting | Verified | F17 |
| P3.4 net worth current/history/comparisons | Verified | F17, F18, F27 |
| P3.5 recurrence, budgets, goals | Verified; budgets covered by e2e, goals UI not | `test_recurrence_monthly_subscription` |
| P4.1 scoped tokens, stdio MCP server, all read tools | Verified | `test_mcp.py`, F24 |
| P4.2 BYOK, env fallback, opt-in, connection test, caps | Verified with a stubbed model | `test_ai_and_tools.py` |
| P4.3 grounded assistant with validated metrics | Verified with a stubbed model | same; **no live Anthropic call was made** (no key supplied) |
| P4.4 classification suggestions, injection tests | Implemented; injection verified | F22 |
| P4.5 sample questions against known answers | Verified at the tool layer | `test_f28_known_answers_via_tools` |
| P5.1 anomalies, forecasts | Implemented; domain tested | forecast/anomaly endpoints |
| P5.2 performance | Verified locally | table above |
| P5.3 security regression | Verified | `tests/security/*` |
| P5.4 backup/restore, key rotation, queue recovery | Verified locally | drills above; deployment rollback unverified |
| P5.5 documentation | Done | `README.md`, `docs/*`, `docs/openapi.json` |

## Deviations from the plan (and why)

See `docs/architecture.md` → "Decisions that differ": owner auth in the API instead of Supabase
Auth; a PostgreSQL jobs table instead of pgmq; encrypted uploads in the database instead of
Supabase Storage; npm instead of pnpm; hand-written components instead of shadcn/ui.

## Bugs found by end-to-end runs (fixed, with regression tests)

- Login rejected `.local` addresses the owner CLI accepts.
- Onboarding redirected back to itself (stale cached profile).
- Dashboard showed an empty current month with misleading comparisons.
- Restore failed to rebuild the search index (extension outside the dumped schema).
- Importing a card statement after the bank statement crashed: the review-item upsert used a
  bound parameter in its partial-index predicate, which PostgreSQL cannot match once psycopg
  prepares the statement (`tests/integration/test_prepared_statements.py`).
- Budgets could not be started in a past month.

## Recent additions (28 Sep 2026)

- Every transaction shows the statement row it came from (file, row number, original columns in file order) and why it has its category (rule name, merchant, pattern, or AI model and confidence).
- Tags can be created on a transaction, renamed and deleted (migration `0006_tag_delete`).
- Transactions: quick filters, multi-select with one-step "Set category" (`POST /transactions/classify-bulk`), and "Categorise with AI" when uncategorised rows exist.
- Import mapping shows the first rows of the file with the chosen columns labelled; more bank header names are recognised; Dr/Cr columns are no longer mistaken for withdrawals.
- AI setup is a three-step flow with a status badge; "Ask" appears in the menu only once the assistant is on.
- Invite links (Settings → People) let someone create their own separate ledger; migration `0007_invites`.
- Sign-in shows signed-out and session-expired notices and a password toggle; Settings can sign out all other devices.

## Additions (29 Sep 2026, after the product review)

Correctness fixes, each with regression tests:

- **EMIs are matched to loans automatically** (`tests/integration/test_loan_matching.py`): debits that
  are exactly the EMI near a due date are split into principal and interest when the loan is added and
  after every import; unclear ones go to Review; lender figures replace estimates; "Not this loan"
  undoes it. Previously the whole EMI counted as spending unless linked by hand.
- **A card bill appears once in Recurring**, named after the card; the receiving side of any transfer is
  no longer a recurring item; stale suggestions are pruned (`test_recurring_transfers.py`, migration `0010`).
- **ATM withdrawals move into a cash wallet** when you keep one (`test_cash_wallet.py`, migration `0011`).
- **Net worth history starts from recorded balances**, not the first daily snapshot (`test_net_worth_history.py`).
- Messages say "1 account" / "3 accounts" instead of "account(s)"; recurring names keep acronyms (SBI, ATM, SIP).

New features: quick add (expense / income / transfer, amounts as sums, saved templates, save and add
another, phone "+" button); transactions calendar, month bar with totals and daily subtotals; Stats
(week/month/year, category donut with sub-category drill-down, daily spending, cash-flow diagram);
Home month switcher, safe to spend, bills coming up, budgets at a glance and alerts; alerts bell;
budget suggestions and rollover; goals editing and progress rings; card billing dates and next bill;
loan summary, yearly principal/interest chart and full schedule; account balance chart; shared
expenses with people and settling up; receipt attachments; SIP-to-holding, AMFI mutual fund prices and
annual return; change password, two-step sign-in from Settings, grouped sessions; full JSON export;
installable web app (manifest and icons); three more read-only AI/MCP tools (upcoming bills, safe to
spend, shared balances). Migration `0012` adds templates, attachments, people and alert dismissals;
`tests/security/test_rls_coverage.py` checks every owner table forces row-level security.

## Additions (30 Sep 2026)

- **Phone alerts through ntfy** (Settings → Phone alerts, migration `0013`). Bills (card bills, loan
  EMIs, confirmed recurring payments) three days ahead and again on the day at urgent priority, plus the
  in-app alerts, pushed to the free ntfy app within about a minute by the scheduler tick. Each alert is
  sent once; quiet hours 22:00–07:00 hold them until morning; "Show amounts" off sends a generic text;
  failures are shown in Settings and retried. The topic is random, encrypted, and replaceable
  (`tests/integration/test_phone_alerts.py`).
- **Deleting one side of a settlement removes both.** Deleting the person's side of a "settle up"
  left the bank payment behind, lowering net worth; a derived counterpart, or a manual entry created in
  the same action, is now deleted with it. Imported statement rows are never deleted
  (`test_deleting_either_side_of_a_settlement_removes_both`).
- **Net worth names what people owe.** "Sanjeev owes you" is listed under what you own and "You owe
  Rahul" under what you owe (it used to be a negative asset); these balances are exact, so never "out of
  date" (`test_net_worth_lists_what_people_owe_you_and_what_you_owe_them`).

## Polish (1 Oct 2026)

- **The import page suggests the account** from the file: a number ending above the transaction table
  (never from transaction descriptions), the same columns as an earlier import, or a known bank layout with
  one account at that bank. The file is read in memory and not kept (`POST /imports/detect-account`,
  `tests/providers/test_account_detect.py`, `tests/integration/test_import_detect.py`).
- **Adding a credit card from Accounts or onboarding asks for its billing dates** (statement day, days to
  pay, limit), so card bills can be estimated and alerted without a statement.
- **Forgot password** on the sign-in page explains the reset: `make reset-password EMAIL=…` (and
  `make reset-mfa`) on the server; there are no reset emails.
- **Shorter transaction list on phones:** two lines per row; the monogram, Recurring badge and tags
  show from tablet width and in the transaction sheet (a month: about 3,200 px instead of 4,050).

## Known limitations

- No bank connections: data comes only from imported statements and manual entries.
- Hosted deployment (Vercel + Supabase + cron) and the Docker images were written but not run.
- On Vercel, uploads are capped near 4 MB by the platform request limit.
- PDF import supports one verified template only; no OCR.
- Cross-currency transfers and many-to-many transfer matching are manual; totals are per currency.
- Realised gains need lot tracking (not modelled); sales make gains "unknown".
- The AI assistant has only been tested with a stubbed model and a fake HTTP transport; no live provider call has been made.
- Remote (HTTP) MCP with OAuth is not implemented; MCP is local stdio.
- No passkeys (WebAuthn) yet; sign-in is password plus optional TOTP.
- Phone alerts need the worker running (`make dev` starts it) and go through ntfy.sh unless you run
  your own ntfy server; there is no SMS. Unconfirmed recurring payments don't alert.
- No self sign-up: new people join through invite links or `make owner`.
- The installed web app has no service worker by design (financial data is never cached on the
  device), so it needs a connection.
- Settling up links a statement credit through the API (`transaction_id`); the People screen adds
  settlements by hand.
- Mutual fund prices need the AMFI scheme code and units; other instruments use recorded values.

## Next recommended steps

1. Deploy to a Supabase + Vercel preview with synthetic data and record the P0.3/P0.4 hosted checks.
2. Supply an Anthropic key and run the P4.5 question set live; tune the system prompt.
3. Add bank-specific import templates from your real (redacted) statement layouts.
