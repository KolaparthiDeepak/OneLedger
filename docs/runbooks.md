# Runbooks

All commands run from the repository root with `.env` loaded (the Makefile does this).

## Local start / stop / reset
- Start: `make dev` (Postgres in `.data/pg`, API :8000, worker, web :3000). Stop: Ctrl+C, then `make db-stop`.
- Reset local data: `make db-stop && rm -rf .data/pg && make db-start migrate && uv run oneledger-admin setup-db-role`.
  Check `DATABASE_URL` points at `localhost` before any reset; never reuse production URLs in `.env`.

## Owner access
- Forgot password: `uv run oneledger-admin reset-password --email you@example.com` (revokes all sessions).
- Lost MFA device: `uv run oneledger-admin reset-mfa --email ...` (operator with DB access only;
  revokes sessions; re-enrol immediately). There is no HTTP bypass.
- Locked out after failed attempts: wait 15 minutes, or reset the password.

## Deployment / rollback
See `deployment.md`. Roll back web/API by promoting the previous Vercel deployment. Before a
migration: `make backup`. If a migration fails, the transaction rolls back; fix forward.

## Stuck or failed jobs
- Status: `GET /api/v1/jobs/{id}`; imports show job status on their page.
- Expired leases are reclaimed automatically on the next tick (60 s lease).
- Failed import commit: open the import and press **Retry import** (nothing was published).
- Nothing progressing hosted: check `cron.job_run_details` in Supabase and the runner's 200s in
  API logs; locally, make sure `oneledger-worker` is running.

## Duplicates and balance mismatches
- Review → Possible duplicates: **Merge into one** (reversible from the transaction's history) or **Both are real**.
- Review → Balances that don't match: shows expected vs statement balance. Usually a missing or
  duplicated statement period — import the gap or merge the duplicate, then record the balance
  again. OneLedger never inserts balancing entries.

## Raw data retention
Uploaded statement files are purged daily after `RAW_RETENTION_DAYS` by the
maintenance job. To purge sooner, lower the value; transactions are unaffected.

## AI key replace / delete
Settings → AI assistant: paste a new key to replace, **Delete my key** to remove. Turning both AI
switches off withdraws consent (opt-in is cleared). `AI_ENABLED=false` disables AI server-wide.

## API tokens (MCP)
Create: `make token EMAIL=you@example.com` (read-only, 90 days; `DETAIL=1` adds raw descriptions).
Revoke: `uv run oneledger-admin revoke-token --email you@example.com --prefix olt_abcd` (effective
immediately). Rotating `TOKEN_HASH_KEY` revokes every token and session at once.

## Inviting people and signed-in devices
Settings → People creates a one-time invite link (7 days); **Revoke** cancels an unused one.
Settings → Sign-in → **Sign out all other devices** ends every other session.

## Backup and restore
- Backup: `make backup` → `backups/*.dump` + `.sha256` (store encrypted, separate from keys).
- Restore drill: `make restore FILE=backups/x.dump TARGET=oneledger_restore` restores into an
  isolated database, verifies checksum, prints schema version and counts. Compare dashboard
  totals against the source, then switch `DATABASE_URL`. Disable the scheduler during recovery.
  Targets: RPO ≤ 24 h, RTO ≤ 4 h (drill measured locally: seconds for a small ledger).

## Encryption key rotation
1. Generate a key; set `SECRET_ENCRYPTION_KEY=<new>`, `SECRET_KEY_VERSION=<n+1>`,
   `PREVIOUS_SECRET_ENCRYPTION_KEYS=<n>:<old>`; deploy.
2. `uv run oneledger-admin rotate-encryption-key` (uses `MIGRATION_DATABASE_URL`).
3. Remove `PREVIOUS_SECRET_ENCRYPTION_KEYS`; deploy. Keep the old key in the recovery store until
   no backup encrypted under it remains.

## Suspected data exposure
See `security.md` → "Suspected exposure": revoke sessions/tokens, rotate `TOKEN_HASH_KEY`,
`SECRET_ENCRYPTION_KEY`, DB passwords, `SCHEDULER_SECRET`, `BFF_SHARED_SECRET`, the AI key, and
review Settings → Activity history.
