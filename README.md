# OneLedger

A private personal finance ledger for one person, or a few people with separate ledgers. It turns
bank, card, loan, investment and cash activity into one ledger with correct transfer, refund and
EMI handling, shows where your money goes, and lets an AI assistant read (never change) your
finances through MCP.

- **Works without any bank API.** Import CSV, Excel or verifiable text-PDF statements; re-importing
  never duplicates, and deleting an import removes everything it added.
- **Numbers you can trust.** Transfers between your accounts and credit-card bill payments are
  never counted as spending; EMIs split into principal and interest; every total links to the
  transactions behind it and says when data is incomplete.
- **Categorised the way you'd do it.** Your choices always win, then your rules, remembered
  merchants and built-in Indian patterns (UPI notes, bills, merchants), then optional AI. Every
  transaction shows the statement row it came from and why it has its category. Tags, splits and
  bulk "set category" included.
- **Private by design.** Invite-only accounts, row-level security in PostgreSQL, encrypted secrets
  and uploads, logs without financial data, AI off by default. Optional TOTP two-step sign-in
  (`REQUIRE_MFA=true`, required in production).

## Quick start (local, no Docker)

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, PostgreSQL 15+
binaries (`initdb`, `pg_ctl`, `psql` on PATH).

```bash
make setup                                    # deps, .env with fresh secrets, local DB, migrations, runtime role
make owner EMAIL=you@example.com NAME="You"   # prompts for a password (min 12 chars)
make dev                                      # API :8000, worker, web :3000
```

Open http://localhost:3000, sign in, add an account and import a statement. Synthetic statements
to try are in `tests/fixtures/`.

Docker alternative: `make setup` (for `.env`), then `make compose-up` (see `docker-compose.yml`).

## More than one person

Everyone gets their own separate ledger; the database enforces that nobody can see anyone else's
data. There is no public sign-up: a signed-in person invites someone from **Settings → People**.
The link works once, expires after 7 days, can be tied to one email, and can be revoked. The
invitee opens it, sets a name and password, and lands in their own empty ledger. You can also
create an account from a terminal with `make owner EMAIL=... NAME=...`.

Whoever runs the server can read every ledger in the database, and one backup contains everyone.

## Tests

```bash
make test        # 124 backend tests against real PostgreSQL (database oneledger_test)
make lint typecheck
make test-e2e    # 9 Playwright journeys: full stack on a throwaway oneledger_e2e database
```

Working on the UI? `./scripts/design_lab.sh` runs a hot-reloading copy (web :3010, API :8010) on
the synthetic e2e database (seed it with `make test-e2e` first), and
`cd apps/web && node scripts/gallery.mjs` screenshots every page in light, dark and mobile into
`.data/gallery/`. Neither touches your real data.

## Using it with an AI client (MCP)

1. Create a read-only token (printed once; add `DETAIL=1` to also allow raw descriptions):
   `make token EMAIL=you@example.com`. Revoke with
   `uv run oneledger-admin revoke-token --email you@example.com --prefix olt_abcd`.
2. Add to your MCP client configuration (e.g. Claude Desktop):

```json
{
  "mcpServers": {
    "oneledger": {
      "command": "uv",
      "args": ["--directory", "/path/to/OneLedger", "run", "oneledger-mcp"],
      "env": { "ONELEDGER_API_URL": "http://localhost:8000", "ONELEDGER_READ_TOKEN": "olt_..." }
    }
  }
}
```

Tools: `get_accounts`, `get_account_balance`, `get_all_balances`, `get_transactions`,
`search_transactions`, `get_income`, `get_expenses`, `get_spending_by_category`,
`get_monthly_summary`, `compare_periods`, `get_cash_flow`, `get_recurring_transactions`,
`get_loans`, `get_loan_summary`, `get_investments`, `get_net_worth`, `find_anomalies`,
`get_financial_goals`. All read-only, enforced by the API. Granting a token lets that AI client
read your finances under its own data policy.

The built-in assistant (Ask) is optional: set `AI_ENABLED=true` in `.env` and restart, then in
Settings → AI assistant choose a provider, paste its API key, pick a model and opt in.
Supported: **OpenRouter** (default, one key for many models; default model `anthropic/claude-opus-5`),
Anthropic, OpenAI, Google Gemini, Groq, DeepSeek, Mistral, Together, local Ollama, and one
OpenAI-compatible endpoint set by the operator (`AI_CUSTOM_BASE_URL`). Keys are stored encrypted
per provider. The assistant needs a model that supports tool calling.

## Project layout

```
apps/api          FastAPI service, job worker, admin CLI       (oneledger_api)
apps/web          Next.js web app with server-side BFF
apps/mcp          Read-only MCP server (stdio)                  (oneledger_mcp)
packages/         finance-domain, categorization, providers, database, shared
infrastructure/   Alembic migrations, Dockerfiles, Supabase SQL
tests/            unit, integration, providers, security, fixtures
docs/             architecture, semantics, security, imports, deployment, runbooks, status, openapi.json
scripts/          local setup, backup/restore, e2e runner, design lab
```

## Documentation

| Topic | File |
|---|---|
| Architecture, ERD, jobs, design decisions | [docs/architecture.md](docs/architecture.md) |
| How income, spending, transfers, loans and net worth are calculated | [docs/financial-semantics.md](docs/financial-semantics.md) |
| Security controls, threat model, retention | [docs/security.md](docs/security.md) |
| Import formats, bank presets, where to download statements | [docs/imports.md](docs/imports.md) |
| Deploying to Vercel + Supabase | [docs/deployment.md](docs/deployment.md) |
| Operations: backup/restore, key rotation, recovery | [docs/runbooks.md](docs/runbooks.md) |
| What is done, verified, and known limitations | [docs/implementation-status.md](docs/implementation-status.md) |
| REST API (OpenAPI 3) | [docs/openapi.json](docs/openapi.json); live at `/api/docs` locally |

## Environment variables

Every variable is documented in [.env.example](.env.example). Secrets are never committed;
`scripts/setup_local.py` generates local ones. Startup refuses unsafe production settings.

## Backup and restore

`make backup` writes a checksummed dump to `backups/`; `make restore FILE=... TARGET=...`
restores into an isolated database and verifies it. Keep encryption keys separate from backups.
