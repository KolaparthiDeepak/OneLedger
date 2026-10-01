# Deployment

**Status: deployed and verified on 1 Oct 2026** — Vercel (API + web) and Supabase (database and
scheduler). The steps below are what was actually done, including the Supabase-specific fixes found
on the way. Record each later deployment's checks in `implementation-status.md`.

## Topology (as deployed)

| Piece | Where | Notes |
|---|---|---|
| Database | Supabase project `personal-apps` (ref `hqcvkqiuwdbirqyrlrsv`, Tokyo `ap-northeast-1`), schema `oneledger` | Shared with another app; OneLedger keeps everything in its own schema, roles and migration table. The schema is **not** exposed through the Supabase Data API, and `anon` / `authenticated` / `service_role` have no access to it. |
| API | Vercel project `oneledger-api` → https://oneledger-api.vercel.app | Python 3.13, `api/index.py` + `vercel.json`; functions run in Tokyo (`hnd1`), next to the database. |
| Web | Vercel project `oneledger-web` → https://oneledger-web.vercel.app | Next.js from `apps/web`; functions in `hnd1`. |
| Scheduler | Supabase `pg_cron` + `pg_net` | Calls `POST /api/internal/runner` every minute with `SCHEDULER_SECRET` (kept in Supabase Vault). Runs maintenance and sends phone alerts. |
| MCP | Your computer | Talks to the hosted API with a read-only token. |

Secrets live in `.env.production` at the repository root (git-ignored) and in the Vercel projects'
environment variables. Keep a copy of `.env.production` in a password manager: losing
`SECRET_ENCRYPTION_KEY` loses encrypted data (AI keys, statement files, receipts, phone-alert topic).

## Database roles

| Role | Login | Used by | Rights |
|---|---|---|---|
| `oneledger_migrator` | yes | `alembic upgrade head` from your computer | Owns the `oneledger` schema and tables; `CREATEROLE`; no superuser, no `BYPASSRLS`. |
| `oneledger_app` | no | group role | Table grants for the runtime. |
| `oneledger_runtime` | yes | the API (`DATABASE_URL`) | Member of `oneledger_app`; no `BYPASSRLS`; every query is filtered by row-level security. |
| `postgres` | Supabase | owns the `SECURITY DEFINER` functions | Has `BYPASSRLS` on Supabase; may update `jobs` and `invites` only. |

Why `postgres` owns the definer functions: sign-in, session and API-token lookup, invites and the
scheduler's job claim must see past row-level security. Locally the migration user is a superuser;
on Supabase the migration login is not, so the functions are handed to `postgres` after each
migration (`after_migrate_postgres.sql`) and the table owner grants `postgres` the two writes they
need (`after_migrate_owner.sql`). Without this, every sign-in fails with "Invalid email or
password" and the scheduler plans nothing.

## Steps (first deployment)

Connection strings use Supabase's pooler, which works over IPv4: port **5432** (session mode) for
migrations and admin commands, port **6543** (transaction mode) for the runtime.

1. **Database.** Use a Supabase project (a free organisation allows two active projects). Do not add
   `oneledger` to *API → Exposed schemas*.
2. **Migration login** (SQL editor, as postgres):
   `create role oneledger_migrator login password '<random>' createrole nosuperuser nobypassrls nocreatedb;`
   `grant create on database postgres to oneledger_migrator;`
3. **`.env.production`**: `APP_ENV=production`, `DATABASE_URL` (runtime, port 6543,
   `sslmode=require`), `DATABASE_POOL_MODE=transaction`, `MIGRATION_DATABASE_URL` (migrator, port
   5432), fresh `SECRET_ENCRYPTION_KEY` and `TOKEN_HASH_KEY` (32 random bytes, urlsafe base64 each),
   `SECRET_KEY_VERSION=1`, `SCHEDULER_SECRET` and `BFF_SHARED_SECRET` (long random strings),
   `REQUIRE_MFA=true`, `COOKIE_SECURE=true`, `MAX_UPLOAD_BYTES=4000000` (Vercel's request limit),
   `AI_ENABLED=false`. Pooler user names have the form `<role>.<project-ref>`.
4. **Migrate:** `set -a; . ./.env.production; set +a; uv run alembic upgrade head`.
5. **After every migration:** run `infrastructure/deployment/supabase/after_migrate_postgres.sql`
   in the SQL editor, then `after_migrate_owner.sql` as the migrator (see the file for the command).
6. **Runtime login** (as the migrator): `runtime_role.sql` with a generated password.
7. **Vercel projects:** `oneledger-api` (framework *Other*, repository root) and `oneledger-web`
   (Next.js). Set both to the Tokyo function region (`hnd1`) so the API sits next to the database;
   the default US region added about 3 s to every request.
8. **Environment variables** (Production scope; secrets as *Sensitive*):
   - API: everything in `.env.production` except `MIGRATION_DATABASE_URL`, plus
     `APP_BASE_URL` and `ALLOWED_ORIGINS` = the web URL and `API_BASE_URL` = the API URL.
   - Web: `ONELEDGER_API_INTERNAL_URL` (API URL), `BFF_SHARED_SECRET`, `ALLOWED_ORIGINS` and
     `APP_BASE_URL` (web URL), `COOKIE_SECURE=true`, `SESSION_TTL_HOURS=12`. Nothing is `NEXT_PUBLIC_`.
9. **Deploy from a clean export of `main`**, never from your working folder (it holds `.env`,
   `.env.production`, `.data/` and `backups/`, which the Vercel CLI would upload):
   ```bash
   D=$(mktemp -d) && git archive main | tar -x -C "$D"
   printf 'apps/web\ntests\ndocs\nscripts\n' > "$D/.vercelignore"
   (cd "$D" && vercel link --project oneledger-api --yes && vercel deploy --prod --yes)
   (cd "$D/apps/web" && vercel link --project oneledger-web --yes && vercel deploy --prod --yes)
   ```
   Check `GET https://oneledger-api.vercel.app/api/ready` returns `{"status":"ready","schema":"0013"}`
   (the latest migration).
10. **Owner:** `set -a; . ./.env.production; set +a; uv run oneledger-admin create-owner --email you@example.com --name "Your name"`
    (asks for the password). Sign in; the first sign-in shows a QR code for an authenticator app.
11. **Scheduler:** edit and run `infrastructure/deployment/supabase/scheduler.sql`. Confirm
    `select * from net._http_response order by created desc limit 3` shows status 200 and
    `"planned":{"maintenance":1,...}` once an owner exists.
12. **Backups:** the free plan has daily backups only, no point-in-time restore. Also run
    `scripts/backup.sh` against the migration URL on a schedule, to storage you control, and run a
    restore drill (`scripts/restore.sh`) before relying on it.

## Checks run on 1 Oct 2026 (all passed)

- `/api/health` and `/api/ready` (schema `0013`); requests enter at Mumbai and run in Tokyo (~0.3 s warm).
- Without a session or with a fake token, API routes return 401; the runner rejects a missing or
  wrong scheduler secret; API responses carry `Cache-Control: no-store`; `/docs` and
  `/openapi.json` are not served.
- Web: signed-out pages redirect to sign-in; HSTS, CSP, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `nosniff`; a cross-site sign-in POST is rejected (`ORIGIN_REJECTED`);
  wrong credentials get a generic message.
- Supabase `anon`, `authenticated` and `service_role` have no `USAGE` on the `oneledger` schema;
  52 of 55 tables force row-level security (the rest hold no owner data).
- Scheduler: a tick returns 200, plans maintenance for the owner, and the job succeeds.

## Updating

1. Merge to `main`. If there are migrations: back up, run `alembic upgrade head` with
   `.env.production`, then both `after_migrate_*.sql` files.
2. Deploy both projects from a clean export (step 9).

## Rollback

Web/API: promote the previous Vercel deployment. Migrations are forward-only in production; take a
backup before every migration that transforms data.

## Docker alternative

`make compose-up` runs Postgres, migrations, API, worker and web behind `127.0.0.1` ports. Put a TLS
reverse proxy in front for anything beyond localhost. (Not exercised.)
