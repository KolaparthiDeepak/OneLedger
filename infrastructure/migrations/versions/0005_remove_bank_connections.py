"""Remove bank connections (Account Aggregator consents, linked provider accounts, sync history).

OneLedger now gets data only from imported statements and manual entry. Drops the consent/sync
tables, the two columns that could only reference their raw records, and the scheduler's
due-sync function. Accounts, imports and transactions are untouched.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "oneledger"
# Children first so foreign keys never block a drop.
TABLES = ["raw_financial_records", "financial_data_fetches", "sync_errors", "account_connections", "sync_runs", "consents"]


# Columns elsewhere that could only point at bank-sync raw records (always empty without sync).
LINKS = [("transaction_sources", "raw_record_id"), ("credit_card_statements", "source_record_id")]


def upgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {S}.due_sync_connections()")
    for table, col in LINKS:
        op.execute(f"ALTER TABLE {S}.{table} DROP COLUMN IF EXISTS {col}")
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {S}.{t}")


# Exact definitions (columns, constraints, row-level security, grants) as they were before 0005.
_RECREATE = r"""
CREATE TABLE oneledger.account_connections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    provider character varying(40) NOT NULL,
    consent_id uuid,
    remote_account_ref_enc bytea NOT NULL,
    remote_account_hash character varying(64) NOT NULL,
    masked_identifier character varying(32),
    account_id uuid,
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,
    capabilities jsonb DEFAULT '{}'::jsonb NOT NULL,
    checkpoint jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_synced_at timestamp with time zone,
    next_run_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
ALTER TABLE ONLY oneledger.account_connections FORCE ROW LEVEL SECURITY;
CREATE TABLE oneledger.consents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    provider character varying(40) NOT NULL,
    provider_reference character varying(128),
    status character varying(32) NOT NULL,
    purpose character varying(200) NOT NULL,
    data_types character varying(40)[] NOT NULL,
    fi_types character varying(40)[] NOT NULL,
    institution_name character varying(120),
    date_from date NOT NULL,
    date_to date NOT NULL,
    frequency character varying(40) NOT NULL,
    data_life_days integer,
    expires_at timestamp with time zone NOT NULL,
    locally_paused boolean DEFAULT false NOT NULL,
    callback_state_hash character varying(64),
    scope_confirmed_at timestamp with time zone NOT NULL,
    activated_at timestamp with time zone,
    revoked_at timestamp with time zone,
    last_status_check_at timestamp with time zone,
    last_synced_at timestamp with time zone,
    status_detail character varying(200),
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_consents_consent_status CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'ACTIVE'::character varying, 'REJECTED'::character varying, 'EXPIRED'::character varying, 'REVOCATION_PENDING'::character varying, 'REVOKED'::character varying, 'ERROR'::character varying])::text[])))
);
ALTER TABLE ONLY oneledger.consents FORCE ROW LEVEL SECURITY;
CREATE TABLE oneledger.financial_data_fetches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    consent_id uuid NOT NULL,
    sync_run_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    provider_session_id character varying(128),
    date_from date NOT NULL,
    date_to_exclusive date NOT NULL,
    status character varying(20) NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    cursor character varying(200),
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
ALTER TABLE ONLY oneledger.financial_data_fetches FORCE ROW LEVEL SECURITY;
CREATE TABLE oneledger.raw_financial_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    fetch_id uuid,
    import_id uuid,
    row_location character varying(200) NOT NULL,
    content_hash character varying(64) NOT NULL,
    parser_version character varying(40) NOT NULL,
    payload_enc bytea,
    retrieved_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone,
    purged_at timestamp with time zone
);
ALTER TABLE ONLY oneledger.raw_financial_records FORCE ROW LEVEL SECURITY;
CREATE TABLE oneledger.sync_errors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    sync_run_id uuid,
    job_id uuid,
    error_code character varying(60) NOT NULL,
    retryable boolean NOT NULL,
    diagnostic_ref character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);
ALTER TABLE ONLY oneledger.sync_errors FORCE ROW LEVEL SECURITY;
CREATE TABLE oneledger.sync_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    consent_id uuid,
    provider character varying(40) NOT NULL,
    run_key character varying(200) NOT NULL,
    trigger character varying(20) NOT NULL,
    date_from date NOT NULL,
    date_to_exclusive date NOT NULL,
    status character varying(20) NOT NULL,
    counters jsonb DEFAULT '{}'::jsonb NOT NULL,
    checkpoint jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_ms integer,
    job_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
ALTER TABLE ONLY oneledger.sync_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE ONLY oneledger.account_connections
    ADD CONSTRAINT pk_account_connections PRIMARY KEY (id);
ALTER TABLE ONLY oneledger.consents
    ADD CONSTRAINT pk_consents PRIMARY KEY (id);
ALTER TABLE ONLY oneledger.financial_data_fetches
    ADD CONSTRAINT pk_financial_data_fetches PRIMARY KEY (id);
ALTER TABLE ONLY oneledger.raw_financial_records
    ADD CONSTRAINT pk_raw_financial_records PRIMARY KEY (id);
ALTER TABLE ONLY oneledger.sync_errors
    ADD CONSTRAINT pk_sync_errors PRIMARY KEY (id);
ALTER TABLE ONLY oneledger.sync_runs
    ADD CONSTRAINT pk_sync_runs PRIMARY KEY (id);
ALTER TABLE ONLY oneledger.account_connections
    ADD CONSTRAINT uq_account_connections_owner_id_provider_remote_account_hash UNIQUE (owner_id, provider, remote_account_hash);
ALTER TABLE ONLY oneledger.raw_financial_records
    ADD CONSTRAINT uq_raw_financial_records_owner_id_fetch_id_row_location UNIQUE (owner_id, fetch_id, row_location);
ALTER TABLE ONLY oneledger.sync_runs
    ADD CONSTRAINT uq_sync_runs_owner_id_run_key UNIQUE (owner_id, run_key);
CREATE INDEX ix_account_connections_account_id ON oneledger.account_connections USING btree (account_id);
CREATE INDEX ix_account_connections_consent_id ON oneledger.account_connections USING btree (consent_id);
CREATE INDEX ix_account_connections_owner_id ON oneledger.account_connections USING btree (owner_id);
CREATE INDEX ix_consents_owner_id ON oneledger.consents USING btree (owner_id);
CREATE INDEX ix_financial_data_fetches_connection_id ON oneledger.financial_data_fetches USING btree (connection_id);
CREATE INDEX ix_financial_data_fetches_consent_id ON oneledger.financial_data_fetches USING btree (consent_id);
CREATE INDEX ix_financial_data_fetches_owner_id ON oneledger.financial_data_fetches USING btree (owner_id);
CREATE INDEX ix_financial_data_fetches_sync_run_id ON oneledger.financial_data_fetches USING btree (sync_run_id);
CREATE INDEX ix_raw_financial_records_fetch_id ON oneledger.raw_financial_records USING btree (fetch_id);
CREATE INDEX ix_raw_financial_records_import_id ON oneledger.raw_financial_records USING btree (import_id);
CREATE INDEX ix_raw_financial_records_owner_id ON oneledger.raw_financial_records USING btree (owner_id);
CREATE INDEX ix_sync_errors_job_id ON oneledger.sync_errors USING btree (job_id);
CREATE INDEX ix_sync_errors_owner_id ON oneledger.sync_errors USING btree (owner_id);
CREATE INDEX ix_sync_errors_sync_run_id ON oneledger.sync_errors USING btree (sync_run_id);
CREATE INDEX ix_sync_runs_consent_id ON oneledger.sync_runs USING btree (consent_id);
CREATE INDEX ix_sync_runs_job_id ON oneledger.sync_runs USING btree (job_id);
CREATE INDEX ix_sync_runs_owner_id ON oneledger.sync_runs USING btree (owner_id);
ALTER TABLE ONLY oneledger.account_connections
    ADD CONSTRAINT fk_account_connections_account_id_accounts FOREIGN KEY (account_id) REFERENCES oneledger.accounts(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.account_connections
    ADD CONSTRAINT fk_account_connections_consent_id_consents FOREIGN KEY (consent_id) REFERENCES oneledger.consents(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.account_connections
    ADD CONSTRAINT fk_account_connections_owner_id_users FOREIGN KEY (owner_id) REFERENCES oneledger.users(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.consents
    ADD CONSTRAINT fk_consents_owner_id_users FOREIGN KEY (owner_id) REFERENCES oneledger.users(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.financial_data_fetches
    ADD CONSTRAINT fk_financial_data_fetches_connection_id_account_connections FOREIGN KEY (connection_id) REFERENCES oneledger.account_connections(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.financial_data_fetches
    ADD CONSTRAINT fk_financial_data_fetches_consent_id_consents FOREIGN KEY (consent_id) REFERENCES oneledger.consents(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.financial_data_fetches
    ADD CONSTRAINT fk_financial_data_fetches_owner_id_users FOREIGN KEY (owner_id) REFERENCES oneledger.users(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.financial_data_fetches
    ADD CONSTRAINT fk_financial_data_fetches_sync_run_id_sync_runs FOREIGN KEY (sync_run_id) REFERENCES oneledger.sync_runs(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.raw_financial_records
    ADD CONSTRAINT fk_raw_financial_records_fetch_id_financial_data_fetches FOREIGN KEY (fetch_id) REFERENCES oneledger.financial_data_fetches(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.raw_financial_records
    ADD CONSTRAINT fk_raw_financial_records_import_id_imports FOREIGN KEY (import_id) REFERENCES oneledger.imports(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.raw_financial_records
    ADD CONSTRAINT fk_raw_financial_records_owner_id_users FOREIGN KEY (owner_id) REFERENCES oneledger.users(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.sync_errors
    ADD CONSTRAINT fk_sync_errors_job_id_jobs FOREIGN KEY (job_id) REFERENCES oneledger.jobs(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.sync_errors
    ADD CONSTRAINT fk_sync_errors_owner_id_users FOREIGN KEY (owner_id) REFERENCES oneledger.users(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.sync_errors
    ADD CONSTRAINT fk_sync_errors_sync_run_id_sync_runs FOREIGN KEY (sync_run_id) REFERENCES oneledger.sync_runs(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.sync_runs
    ADD CONSTRAINT fk_sync_runs_consent_id_consents FOREIGN KEY (consent_id) REFERENCES oneledger.consents(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.sync_runs
    ADD CONSTRAINT fk_sync_runs_job_id_jobs FOREIGN KEY (job_id) REFERENCES oneledger.jobs(id) ON DELETE RESTRICT;
ALTER TABLE ONLY oneledger.sync_runs
    ADD CONSTRAINT fk_sync_runs_owner_id_users FOREIGN KEY (owner_id) REFERENCES oneledger.users(id) ON DELETE RESTRICT;
ALTER TABLE oneledger.account_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE oneledger.consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE oneledger.financial_data_fetches ENABLE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON oneledger.account_connections TO oneledger_app USING ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid)) WITH CHECK ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid));
CREATE POLICY owner_isolation ON oneledger.consents TO oneledger_app USING ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid)) WITH CHECK ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid));
CREATE POLICY owner_isolation ON oneledger.financial_data_fetches TO oneledger_app USING ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid)) WITH CHECK ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid));
CREATE POLICY owner_isolation ON oneledger.raw_financial_records TO oneledger_app USING ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid)) WITH CHECK ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid));
CREATE POLICY owner_isolation ON oneledger.sync_errors TO oneledger_app USING ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid)) WITH CHECK ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid));
CREATE POLICY owner_isolation ON oneledger.sync_runs TO oneledger_app USING ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid)) WITH CHECK ((owner_id = (NULLIF(current_setting('app.owner_id'::text, true), ''::text))::uuid));
ALTER TABLE oneledger.raw_financial_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE oneledger.sync_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE oneledger.sync_runs ENABLE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON TABLE oneledger.account_connections TO oneledger_app;
GRANT SELECT,INSERT,UPDATE ON TABLE oneledger.consents TO oneledger_app;
GRANT SELECT,INSERT,UPDATE ON TABLE oneledger.financial_data_fetches TO oneledger_app;
GRANT SELECT,INSERT,UPDATE ON TABLE oneledger.raw_financial_records TO oneledger_app;
GRANT SELECT,INSERT,UPDATE ON TABLE oneledger.sync_errors TO oneledger_app;
GRANT SELECT,INSERT,UPDATE ON TABLE oneledger.sync_runs TO oneledger_app;
"""


def downgrade() -> None:
    op.execute(_RECREATE)
    op.execute(
        f"""
        ALTER TABLE {S}.transaction_sources ADD COLUMN raw_record_id uuid
          CONSTRAINT fk_transaction_sources_raw_record_id_raw_financial_records
          REFERENCES {S}.raw_financial_records(id) ON DELETE RESTRICT;
        CREATE INDEX ix_transaction_sources_raw_record_id ON {S}.transaction_sources (raw_record_id);
        ALTER TABLE {S}.credit_card_statements ADD COLUMN source_record_id uuid
          CONSTRAINT fk_credit_card_statements_source_record_id_raw_financia_84ee
          REFERENCES {S}.raw_financial_records(id) ON DELETE RESTRICT;
        CREATE INDEX ix_credit_card_statements_source_record_id ON {S}.credit_card_statements (source_record_id);
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {S}.due_sync_connections()
        RETURNS TABLE (owner_id uuid, consent_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT DISTINCT c.owner_id, c.consent_id
            FROM {S}.account_connections c JOIN {S}.consents k ON k.id = c.consent_id
           WHERE c.status = 'ACTIVE' AND k.status = 'ACTIVE' AND NOT k.locally_paused
             AND (c.next_run_at IS NULL OR c.next_run_at <= now())
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {S}.due_sync_connections() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {S}.due_sync_connections() TO oneledger_app")
