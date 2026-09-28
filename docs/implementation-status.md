# Implementation status

Last updated: 28 Sep 2026. Latest migration revision: `0002`.

"Verified" means a test or command in this repository exercised it in this session.
"Implemented" means the code exists but was not exercised end to end. "Unverified" means it
depends on infrastructure that was not available.

## Checks run (all passing)

| Check | Command | Result |
|---|---|---|
| Lint / format | `uv run ruff check . && uv run ruff format --check .` | pass (118 files) |
| Types (Python, strict) | `uv run mypy apps/api/src packages` ; `uv run mypy apps/mcp/src` | pass (90 files) |
| Types (web) | `cd apps/web && npx tsc --noEmit` | pass |
| Backend tests (real PostgreSQL 15, runtime role with RLS) | `uv run pytest tests -q` | 124 passed |
| Migrations | `alembic downgrade base` then `upgrade head` on `oneledger_test` | pass |
| Web production build | `npm run build` | pass (26 routes) |
| Browser e2e (Playwright, Chromium) | `./scripts/e2e.sh` | 9 passed (first run + import/re-import, BFF checks, card matching, loan EMI split + prepayment simulation, rules dry-run, budgets/net worth/settings, delete import, tags + bulk categorise, invite a person) |
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

## Known limitations

- No bank connections: data comes only from imported statements and manual entries.
- Hosted deployment (Vercel + Supabase + cron) and the Docker images were written but not run.
- On Vercel, uploads are capped near 4 MB by the platform request limit.
- PDF import supports one verified template only; no OCR.
- Cross-currency transfers and many-to-many transfer matching are manual; totals are per currency.
- Realised gains need lot tracking (not modelled); sales make gains "unknown".
- The AI assistant has only been tested with a stubbed model and a fake HTTP transport; no live provider call has been made.
- Remote (HTTP) MCP with OAuth is not implemented; MCP is local stdio.

## Next recommended steps

1. Deploy to a Supabase + Vercel preview with synthetic data and record the P0.3/P0.4 hosted checks.
2. Supply an Anthropic key and run the P4.5 question set live; tune the system prompt.
3. Add bank-specific import templates from your real (redacted) statement layouts.
