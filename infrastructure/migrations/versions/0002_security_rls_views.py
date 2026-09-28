"""Runtime role, row-level security, integrity triggers, pre-auth functions and reporting views.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "oneledger"

# Tables without per-row owner scope (shared reference data or pre-auth bookkeeping).
UNSCOPED = {"financial_institutions", "rate_limit_counters", "alembic_version"}
# Append-only evidence: runtime role may insert/read but never update/delete.
APPEND_ONLY = {"audit_logs", "transaction_revisions"}
# Tables where the runtime role may physically delete rows (non-financial or derived data).
DELETABLE = {
    "sessions", "idempotency_keys", "rate_limit_counters", "transaction_allocations", "transaction_tag_links",
    "transfer_legs", "recurring_transaction_members", "import_rows", "user_secrets", "review_items",
    "net_worth_snapshots", "anomalies", "report_queries", "goal_contributions",
}
OWNER_COLUMN = {"users": "id"}

OWNER_EXPR = "NULLIF(current_setting('app.owner_id', true), '')::uuid"


def _tables() -> list[str]:
    # The tables that exist at this point in the history (not today's model list, which may include
    # tables created by later migrations; those secure themselves).
    rows = op.get_bind().exec_driver_sql(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'oneledger' AND table_type = 'BASE TABLE'"
    )
    return sorted(r[0] for r in rows)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_transactions_search ON {S}.transactions "
        "USING gin (normalized_description gin_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_transactions_merchant ON {S}.transactions (owner_id, merchant_name)"
    )

    # --- Runtime role -----------------------------------------------------------------------
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'oneledger_app') THEN
            CREATE ROLE oneledger_app NOLOGIN NOBYPASSRLS;
          END IF;
        END $$;
        """
    )
    op.execute(f"REVOKE ALL ON SCHEMA {S} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {S} TO oneledger_app")

    for table in _tables():
        fq = f"{S}.{table}"
        op.execute(f"REVOKE ALL ON {fq} FROM PUBLIC")
        if table in APPEND_ONLY:
            op.execute(f"GRANT SELECT, INSERT ON {fq} TO oneledger_app")
        else:
            op.execute(f"GRANT SELECT, INSERT, UPDATE ON {fq} TO oneledger_app")
        if table in DELETABLE:
            op.execute(f"GRANT DELETE ON {fq} TO oneledger_app")
        if table in UNSCOPED:
            continue
        col = OWNER_COLUMN.get(table, "owner_id")
        op.execute(f"ALTER TABLE {fq} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {fq} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY owner_isolation ON {fq} FOR ALL TO oneledger_app "
            f"USING ({col} = {OWNER_EXPR}) WITH CHECK ({col} = {OWNER_EXPR})"
        )
    op.execute(f"GRANT SELECT ON {S}.alembic_version TO oneledger_app")

    # --- Integrity triggers -----------------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.check_allocation_sum() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          tid uuid;
          txn_amount numeric;
          total numeric;
        BEGIN
          IF TG_TABLE_NAME = 'transactions' THEN
            tid := NEW.id;
          ELSE
            tid := COALESCE(NEW.transaction_id, OLD.transaction_id);
          END IF;
          SELECT amount INTO txn_amount FROM {S}.transactions WHERE id = tid;
          IF NOT FOUND THEN
            RETURN NULL;
          END IF;
          SELECT COALESCE(sum(amount), 0) INTO total FROM {S}.transaction_allocations WHERE transaction_id = tid;
          IF total <> txn_amount THEN
            RAISE EXCEPTION 'ALLOCATION_SUM_MISMATCH' USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        f"""
        CREATE CONSTRAINT TRIGGER allocation_sum_on_allocations
          AFTER INSERT OR UPDATE OR DELETE ON {S}.transaction_allocations
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {S}.check_allocation_sum();
        CREATE CONSTRAINT TRIGGER allocation_sum_on_transactions
          AFTER INSERT OR UPDATE OF amount ON {S}.transactions
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {S}.check_allocation_sum();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.check_category_cycle() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          cur uuid := NEW.parent_id;
          depth int := 0;
        BEGIN
          WHILE cur IS NOT NULL LOOP
            IF cur = NEW.id THEN
              RAISE EXCEPTION 'CATEGORY_CYCLE' USING ERRCODE = '23514';
            END IF;
            depth := depth + 1;
            IF depth > 20 THEN
              RAISE EXCEPTION 'CATEGORY_TOO_DEEP' USING ERRCODE = '23514';
            END IF;
            SELECT parent_id INTO cur FROM {S}.transaction_categories WHERE id = cur;
          END LOOP;
          RETURN NEW;
        END $$;
        CREATE TRIGGER category_cycle BEFORE INSERT OR UPDATE OF parent_id ON {S}.transaction_categories
          FOR EACH ROW EXECUTE FUNCTION {S}.check_category_cycle();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.check_transfer_leg() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          alloc_amount numeric;
        BEGIN
          SELECT amount INTO alloc_amount FROM {S}.transaction_allocations WHERE id = NEW.allocation_id;
          IF alloc_amount IS NULL OR sign(alloc_amount) <> sign(NEW.amount) OR abs(NEW.amount) > abs(alloc_amount) THEN
            RAISE EXCEPTION 'TRANSFER_LEG_EXCEEDS_ALLOCATION' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER transfer_leg_bounds BEFORE INSERT OR UPDATE ON {S}.transfer_legs
          FOR EACH ROW EXECUTE FUNCTION {S}.check_transfer_leg();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.forbid_raw_evidence_rewrite() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.raw IS DISTINCT FROM OLD.raw OR NEW.source_row_number IS DISTINCT FROM OLD.source_row_number THEN
            RAISE EXCEPTION 'RAW_EVIDENCE_IMMUTABLE' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER import_rows_raw_immutable BEFORE UPDATE ON {S}.import_rows
          FOR EACH ROW EXECUTE FUNCTION {S}.forbid_raw_evidence_rewrite();
        """
    )

    # --- Pre-auth / cross-owner functions (SECURITY DEFINER, minimal outputs) -------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.auth_user_by_email(p_email text)
        RETURNS TABLE (id uuid, password_hash text, is_owner_enabled boolean, locked_until timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT u.id, u.password_hash, u.is_owner_enabled, u.locked_until
          FROM {S}.users u WHERE lower(u.email) = lower(p_email)
        $$;
        CREATE OR REPLACE FUNCTION {S}.auth_session_lookup(p_token_hash text)
        RETURNS TABLE (session_id uuid, owner_id uuid, expires_at timestamptz, last_seen_at timestamptz,
                       mfa_verified_at timestamptz, revoked_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT s.id, s.owner_id, s.expires_at, s.last_seen_at, s.mfa_verified_at, s.revoked_at
          FROM {S}.sessions s WHERE s.token_hash = p_token_hash
        $$;
        CREATE OR REPLACE FUNCTION {S}.auth_api_token_lookup(p_token_hash text)
        RETURNS TABLE (token_id uuid, owner_id uuid, scopes text[], expires_at timestamptz, revoked_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT t.id, t.owner_id, t.scopes::text[], t.expires_at, t.revoked_at
          FROM {S}.api_tokens t WHERE t.token_hash = p_token_hash
        $$;
        CREATE OR REPLACE FUNCTION {S}.claim_jobs(p_limit int, p_lease_seconds int)
        RETURNS TABLE (job_id uuid, owner_id uuid, job_type text, lease_generation int, attempts int)
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          UPDATE {S}.jobs j
             SET status = 'RUNNING', lease_generation = j.lease_generation + 1,
                 lease_expires_at = now() + make_interval(secs => p_lease_seconds),
                 attempts = j.attempts + 1, updated_at = now()
           WHERE j.id IN (
             SELECT c.id FROM {S}.jobs c
              WHERE (c.status = 'QUEUED' AND c.next_attempt_at <= now())
                 OR (c.status = 'RUNNING' AND c.lease_expires_at < now())
              ORDER BY c.next_attempt_at
              FOR UPDATE SKIP LOCKED
              LIMIT LEAST(p_limit, 50))
          RETURNING j.id, j.owner_id, j.type::text, j.lease_generation, j.attempts
        $$;
        CREATE OR REPLACE FUNCTION {S}.due_sync_connections()
        RETURNS TABLE (owner_id uuid, consent_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT DISTINCT c.owner_id, c.consent_id
            FROM {S}.account_connections c JOIN {S}.consents k ON k.id = c.consent_id
           WHERE c.status = 'ACTIVE' AND k.status = 'ACTIVE' AND NOT k.locally_paused
             AND (c.next_run_at IS NULL OR c.next_run_at <= now())
        $$;
        CREATE OR REPLACE FUNCTION {S}.enabled_owner_ids()
        RETURNS SETOF uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT id FROM {S}.users WHERE is_owner_enabled
        $$;
        """
    )
    for fn in ("auth_user_by_email(text)", "auth_session_lookup(text)", "auth_api_token_lookup(text)",
               "claim_jobs(int, int)", "due_sync_connections()", "enabled_owner_ids()"):
        op.execute(f"REVOKE ALL ON FUNCTION {S}.{fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {S}.{fn} TO oneledger_app")

    # --- Read-only reporting views over active allocations (no independent copies) -------------
    for name, effect in (("income_records", "income"), ("expense_records", "expense")):
        op.execute(
            f"""
            CREATE VIEW {S}.{name} WITH (security_invoker = true) AS
            SELECT a.id AS allocation_id, a.owner_id, t.id AS transaction_id, t.account_id,
                   t.transaction_date, a.amount, t.currency, a.category_id, a.classification_source,
                   t.merchant_name
              FROM {S}.transaction_allocations a
              JOIN {S}.transactions t ON t.id = a.transaction_id
             WHERE a.effect = '{effect}' AND t.status = 'POSTED' AND t.deleted_at IS NULL
               AND t.merged_into_id IS NULL AND t.is_published
            """
        )
        op.execute(f"GRANT SELECT ON {S}.{name} TO oneledger_app")


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {S}.income_records")
    op.execute(f"DROP VIEW IF EXISTS {S}.expense_records")
    for fn in ("auth_user_by_email(text)", "auth_session_lookup(text)", "auth_api_token_lookup(text)",
               "claim_jobs(int, int)", "due_sync_connections()", "enabled_owner_ids()"):
        op.execute(f"DROP FUNCTION IF EXISTS {S}.{fn}")
    op.execute(f"DROP TRIGGER IF EXISTS import_rows_raw_immutable ON {S}.import_rows")
    op.execute(f"DROP TRIGGER IF EXISTS transfer_leg_bounds ON {S}.transfer_legs")
    op.execute(f"DROP TRIGGER IF EXISTS category_cycle ON {S}.transaction_categories")
    op.execute(f"DROP TRIGGER IF EXISTS allocation_sum_on_transactions ON {S}.transactions")
    op.execute(f"DROP TRIGGER IF EXISTS allocation_sum_on_allocations ON {S}.transaction_allocations")
    for fn in ("forbid_raw_evidence_rewrite()", "check_transfer_leg()", "check_category_cycle()",
               "check_allocation_sum()"):
        op.execute(f"DROP FUNCTION IF EXISTS {S}.{fn}")
    for table in _tables():
        if table in UNSCOPED:
            continue
        op.execute(f"DROP POLICY IF EXISTS owner_isolation ON {S}.{table}")
        op.execute(f"ALTER TABLE {S}.{table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_transactions_merchant")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_transactions_search")
