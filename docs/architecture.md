# Architecture

```
Browser ──► Next.js web (apps/web)
              │  server-side BFF: HttpOnly session cookie → Bearer token, route allowlist, origin check
              ▼
           FastAPI (apps/api) ──► PostgreSQL (schema "oneledger", RLS per owner)
              ▲      │               ▲
   MCP server │      │ jobs table ◄──┘ leased with SKIP LOCKED
 (apps/mcp,   │      ▼
  stdio,      │   Worker / runner (same code): imports, analytics, AI categorisation, maintenance
  read token) │      │
              │      └──► FinancialDataProvider adapters (manual import, OneMoney boundary, mock)
   Local AI client        Optional AI provider (Anthropic) — only after opt-in, read-only tools
```

## Packages

| Package | Responsibility | Depends on |
|---|---|---|
| `oneledger_shared` | Settings validation, errors, AES-GCM secret box, allowlist JSON logging | – |
| `oneledger_domain` | Pure calculations: money parsing, periods, dedup, transfer matching, reporting definitions, loans, net worth, recurrence, anomalies, forecast | shared |
| `oneledger_categorization` | Category tree, typed rules, merchant patterns, categorisation pipeline | domain |
| `oneledger_providers` | Statement parsers: CSV/XLSX/XLS/PDF, column mapping, bank presets | domain |
| `oneledger_db` | SQLAlchemy models, owner-scoped sessions | shared |
| `oneledger_api` | Routes, services (ledger, imports, transfers, reports, products, AI), jobs, CLI | all |
| `oneledger_mcp` | Read-only MCP tools that call `/api/v1/tools/*` | httpx, mcp |

Domain modules import no web or database code. REST, MCP and AI all use the same report
services, so a figure means the same thing everywhere.

## Data model (ERD summary)

```
users 1─* accounts 1─* transactions 1─* transaction_allocations *─1 transaction_categories
                  │            │  └─* transaction_sources ─ import_rows
                  │            ├─* transaction_tag_links *─1 transaction_tags
                  │            ├─* transaction_relations (refund/reversal/merge)
                  │            └─* transaction_revisions (audited corrections)
                  ├─* balance_snapshots
                  ├─1 credit_cards 1─* credit_card_statements
                  ├─1 loans 1─* loan_payments, loan_rate_changes
                  └─1 cash_accounts
transaction_allocations 1─* transfer_legs *─1 transfers
investments 1─* investment_transactions, investment_valuations
imports 1─* import_rows ; imports *─1 import_files (encrypted bytes)
recurring_transactions 1─* recurring_transaction_members ; budgets ; financial_goals 1─* goal_contributions
net_worth_snapshots ; anomalies ; review_items ; report_queries ; ledger_state
jobs ; audit_logs ; sessions ; api_tokens ; user_secrets ; ai_settings ; ai_runs ; idempotency_keys
views: income_records, expense_records (security_invoker, over active allocations)
```

All private tables carry `owner_id` with RLS. Key constraints: allocation sum = transaction amount
(deferred trigger), non-zero amounts, category cycle prevention, transfer legs bounded by their
allocation, unique active provider identity per account, one open review item per dedupe key,
raw import rows immutable.

## Jobs and scheduling

A job row is inserted in the same transaction as the change that needs it and carries only IDs.
`claim_jobs()` leases ready or expired jobs with `FOR UPDATE SKIP LOCKED` and bumps a lease
generation; handlers write data, checkpoints and status in one transaction guarded by that
generation, so redelivery or a stale worker cannot double-apply work. Work is chunked (500 rows,
20-second budget). Transient failures back off exponentially up to 5 attempts; validation
failures fail immediately with a visible error.

Locally `oneledger-worker` loops over the same `tick()` the hosted runner uses. Hosted, Supabase
Cron calls `POST /api/internal/runner` every minute with the scheduler secret. The tick plans daily
maintenance (retention purge, recurrence/anomaly refresh, net-worth snapshot), then drains the queue within the budget.

## Import pipeline

`UPLOADED → NEEDS_MAPPING → PREVIEW_READY → CONFIRMED → COMMITTING → COMPLETED` (or FAILED /
CANCELLED). Parsing and preview write only staging rows. Confirmation requires the current
preview hash. Commit re-checks duplicates under a per-account advisory lock (returns to review
if the ledger changed), inserts in chunks into an unpublished batch, then publishes the batch,
records the statement's closing balance, opens review items, runs transfer matching and schedules
analytics.

## Decisions that differ from the implementation plan

| Plan | Built | Why |
|---|---|---|
| Supabase Auth | Owner auth in the API (Argon2id, TOTP, hashed opaque sessions) | Works fully offline, no Supabase CLI/Docker needed locally, simpler BFF (no third-party token refresh); Supabase remains the hosted Postgres |
| pgmq queue | `jobs` table with leases (`SKIP LOCKED`) | Same durability and idempotency on any PostgreSQL, including Supabase; one less extension |
| Supabase Storage + signed uploads | Uploads go through the API and are stored AES-GCM-encrypted in `import_files` | No object store to provision; backed up with the database; purged on retention. On Vercel, uploads are limited by the ~4.5 MB function payload cap (see deployment) |
| pnpm workspace | npm in `apps/web` | pnpm was not installed; the web app is the only Node package |
| shadcn/ui | Small hand-written accessible components on native `<dialog>` | Fewer dependencies; same accessibility properties |
