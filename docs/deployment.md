# Deployment

**Status: configuration written, not deployed or tested in a hosted environment** (no deployment
access was authorised). Local operation is verified. Treat the steps below as a checklist and
record results in `implementation-status.md`.

## Topology

- Supabase project (production): PostgreSQL only (with `pg_cron`, `pg_net`, Vault).
- Vercel project **oneledger-api**: repository root, Python runtime, `api/index.py` + `vercel.json`.
- Vercel project **oneledger-web**: root directory `apps/web`, Next.js.
- MCP server runs on your computer and talks to the hosted API with a read-only token.

## Steps

1. **Database.** Create the Supabase project in your chosen region. In *Database → Connection
   pooling* note the direct URL (migrations) and the transaction-pooler URL (runtime). Do not add
   the `oneledger` schema to the Data API's exposed schemas.
2. **Migrate** from your machine with the direct URL:
   `MIGRATION_DATABASE_URL='postgresql+psycopg://postgres:...@db.<ref>.supabase.co:5432/postgres?sslmode=require' uv run alembic upgrade head`
3. **Runtime role.** Run `infrastructure/deployment/supabase/runtime_role.sql` with a generated password.
   Runtime URL: `postgresql+psycopg://oneledger_runtime.<ref>:<password>@<pooler-host>:6543/postgres?sslmode=require`
   with `DATABASE_POOL_MODE=transaction`.
4. **API project env** (Production scope only): `APP_ENV=production`, `APP_BASE_URL`,
   `ALLOWED_ORIGINS` (the web URL), `DATABASE_URL`, `DATABASE_POOL_MODE=transaction`,
   `SECRET_ENCRYPTION_KEY`, `SECRET_KEY_VERSION`, `TOKEN_HASH_KEY`, `SCHEDULER_SECRET`,
   `BFF_SHARED_SECRET`, `REQUIRE_MFA=true`, `COOKIE_SECURE=true`, `MAX_UPLOAD_BYTES=4000000`
   (Vercel request limit), AI variables if wanted. Never set `MOCK_PROVIDER_ENABLED` (startup
   refuses it in production). Keep preview deployments pointed at a separate database.
5. **Web project env:** `ONELEDGER_API_INTERNAL_URL` (API URL), `BFF_SHARED_SECRET`,
   `ALLOWED_ORIGINS`, `COOKIE_SECURE=true`, `SESSION_TTL_HOURS`. Nothing is `NEXT_PUBLIC_`.
6. Deploy API, then web. Check `GET /api/ready` returns `{"status":"ready","schema":"0002"}`.
7. **Owner:** `DATABASE_URL=<runtime url> MIGRATION_DATABASE_URL=<direct url> ... uv run oneledger-admin create-owner --email you@example.com`, then sign in and enrol MFA.
8. **Scheduler:** edit and run `infrastructure/deployment/supabase/scheduler.sql`. Confirm
   `cron.job_run_details` shows successful calls and the API logs `POST /api/internal/runner` 200.
9. **Verify** (before loading real data): non-owner/unauthenticated requests are refused,
   responses carry `Cache-Control: private, no-store`, security headers are present, logs show no
   bodies, a synthetic import survives a mid-import redeploy, and a backup restores (step below).
10. **Backups:** enable Supabase PITR/daily backups, and also run `scripts/backup.sh` against the
    direct URL on a schedule to storage you control. Keep encryption keys in a separate secret
    store. Run a restore drill (`scripts/restore.sh`) before relying on it.

Docker alternative: `make compose-up` runs Postgres, migrations, API, worker and web behind
`127.0.0.1` ports. Put a TLS reverse proxy in front for anything beyond localhost.
(The Docker files are written but were not built in this session because Docker was not running.)

## Rollback

Web/API: promote the previous Vercel deployment. Migrations are forward-only in production;
revision `0002` has a tested downgrade, `0001` is the baseline. Take a backup before every
migration that transforms data.
