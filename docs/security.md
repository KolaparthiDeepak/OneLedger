# Security and privacy

## Threat model (summary)

Assets: ledger data, uploaded statements, the owner's session, API/MCP tokens, AI and provider
keys. Adversaries: someone on the internet reaching the deployment, a stolen password or token,
a malicious file or transaction description (including prompt injection), a compromised AI
client, an operator mistake (wrong database, leaked logs).

## Controls

| Area | Control | Where |
|---|---|---|
| Authentication | Owner provisioned by CLI; no signup endpoint. Argon2id passwords, lockout after 10 failures, DB-backed rate limits per IP and per email, timing-equalised unknown-email path | `security/auth.py` |
| MFA | TOTP (secret encrypted). Required in preview/production (`REQUIRE_MFA`); a session without MFA can reach only enrolment. Strong actions (tokens, key storage, full export) require MFA when enabled | `security/auth.py`, `routes/auth.py` |
| Sessions | Opaque random tokens; only an HMAC-SHA256 hash is stored; absolute TTL + idle timeout; revocable. Browser holds them only in an HttpOnly, SameSite=Strict, `__Host-` (when secure) cookie | `apps/web/src/app/api/auth` |
| CSRF | SameSite=Strict + Origin allowlist on every cookie-authenticated mutation in the BFF. The API itself accepts only bearer tokens (no cookies) | `lib/server/security.ts` |
| BFF | Allowlisted route prefixes only; no general proxy; `private, no-store` on every response | `app/api/bff/[...path]` |
| Authorization | Every private table has `owner_id`; PostgreSQL row-level security with `FORCE`, transaction-local owner context, runtime role without BYPASSRLS; composite FKs prevent cross-owner links; unknown/foreign ids return the same 404 | migration `0002`, `oneledger_db/session.py` |
| Least privilege | Runtime role cannot DELETE transactions, sources, raw evidence or audit logs; audit/revision tables are append-only; migrations use a separate credential | migration `0002` |
| API tokens (MCP) | High-entropy, hashed, scoped (`finance:read`, optional `transactions:read-detail`), expiring, revocable; write scopes cannot be issued. Without detail scope, raw descriptions are withheld | `security/auth.py` |
| Secrets at rest | AES-256-GCM with versioned keys (rotation via `PREVIOUS_SECRET_ENCRYPTION_KEYS`) for TOTP secrets, AI keys, uploaded statement files, provider references and raw provider payloads | `oneledger_shared/crypto.py` |
| Logging | JSON logs with an allowlist of fields; exception *types* only; request bodies never logged; account-number-like digits masked | `oneledger_shared/logging.py` |
| Uploads | Magic-byte detection, size/row limits, ZIP-bomb/macro/external-link checks, formulas never evaluated, PDFs declined unless the balance chain verifies; files purged after `RAW_RETENTION_DAYS` | `providers/imports` |
| Exports | Spreadsheet formula injection neutralised | `services/imports._formula_safe` |
| Injection | SQLAlchemy parameters everywhere; LIKE wildcards escaped; rule conditions are typed data, not code or regex | `txn_query.py`, `categorization/rules.py` |
| XSS | React escaping; answers rendered as text; CSP, `X-Frame-Options: DENY`, `nosniff`, no-referrer | `next.config.ts`, API middleware |
| AI | Off by default and needs server enablement + owner opt-in + key. Provider endpoints are fixed in code or set by the operator (`AI_CUSTOM_BASE_URL`), never entered in the browser, and redirects are not followed, so settings cannot be used to reach arbitrary hosts. Keys are encrypted per provider. Read-only tools only. Numbers must be references to server-computed metrics; any other figure → answer withheld. Descriptions shared only if opted in. Descriptions are treated as untrusted data. Daily budget, timeouts, tool-round cap | `services/ai.py` |
| Scheduler | Runner endpoint requires `SCHEDULER_SECRET`; job payloads carry IDs only | `routes/internal.py`, `services/jobs.py` |
| Config | Startup fails on missing/short keys; in preview/production also on wildcard origins, insecure cookies, MFA off, or DB without TLS | `oneledger_shared/config.py` |

## Invites (more than one person)

- Each person's rows carry their own id and are isolated by row-level security, as for the first owner.
- Invite tokens are 32 random bytes, stored only as an HMAC hash, single-use, expire after 7 days,
  optionally bound to one email, and revocable. The link is shown once to the sender.
- Checking and accepting are unauthenticated, so they are rate-limited per client IP and go through
  `SECURITY DEFINER` functions that can only read or consume the one invite whose hash is given.
  Every invalid case returns the same `INVITE_INVALID` answer.
- The account is created and the invite consumed in one transaction; a failed attempt (wrong email,
  email already used, weak password) leaves the invite usable.
- A sender sees only the email an invitee joined with (`invite_joined_email`), never their ledger.

## Tests covering these

`tests/security/test_isolation_auth.py` (cross-owner access through the API and directly as the
runtime role, auth failures, lockout/rate limits, token scopes, runner auth, formula escaping,
malicious uploads, SQL-looking search input), `tests/integration/test_ai_and_tools.py`
(disabled AI makes zero calls, key never returned or logged, invented numbers rejected, injection
text inert), `apps/web/e2e` (BFF origin and route checks).

## Retention and deletion

Uploaded statement bytes and raw provider payloads: purged after `RAW_RETENTION_DAYS` (default
30); metadata and hashes remain, marked expired. Ledger data: until you delete it. Financial edits
are soft deletes or audited corrections. Permanent erasure of the whole database is an operator
action (drop the database and destroy backups/keys) — soft deletion is not erasure.

## Suspected exposure

Sign out all other devices (Settings), revoke API tokens (`oneledger-admin revoke-token`) and
unused invites (Settings → People), rotate `TOKEN_HASH_KEY` (invalidates every
session and token), rotate `SECRET_ENCRYPTION_KEY` (see runbooks), rotate the runtime DB
password and `SCHEDULER_SECRET`, delete and re-enter the AI key, and review the audit history.
