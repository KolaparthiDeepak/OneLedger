"""Saved transaction templates, receipt attachments, people you share expenses with, dismissed alerts,
and the link from a holding to the recurring SIP that funds it.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "oneledger"
OWNER_EXPR = "NULLIF(current_setting('app.owner_id', true), '')::uuid"
TABLES = ("transaction_templates", "transaction_attachments", "people", "alert_dismissals")


def _secure(table: str, *, deletable: bool) -> str:
    fq = f"{S}.{table}"
    grants = "SELECT, INSERT, UPDATE, DELETE" if deletable else "SELECT, INSERT, UPDATE"
    return f"""
        REVOKE ALL ON {fq} FROM PUBLIC;
        GRANT {grants} ON {fq} TO oneledger_app;
        ALTER TABLE {fq} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {fq} FORCE ROW LEVEL SECURITY;
        CREATE POLICY owner_isolation ON {fq} FOR ALL TO oneledger_app
            USING (owner_id = {OWNER_EXPR}) WITH CHECK (owner_id = {OWNER_EXPR});
    """


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {S}.transaction_templates (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id uuid NOT NULL REFERENCES {S}.users(id) ON DELETE RESTRICT,
            name varchar(80) NOT NULL,
            kind varchar(10) NOT NULL CHECK (kind IN ('expense', 'income', 'transfer')),
            account_id uuid REFERENCES {S}.accounts(id) ON DELETE CASCADE,
            to_account_id uuid REFERENCES {S}.accounts(id) ON DELETE CASCADE,
            amount numeric(24, 8) CHECK (amount IS NULL OR amount > 0),
            category_id uuid REFERENCES {S}.transaction_categories(id) ON DELETE SET NULL,
            description varchar(500) NOT NULL,
            use_count integer NOT NULL DEFAULT 0,
            last_used_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_transaction_templates_transfer_accounts
                CHECK (kind <> 'transfer' OR (to_account_id IS NOT NULL AND to_account_id <> account_id))
        );
        CREATE INDEX ix_transaction_templates_owner_id ON {S}.transaction_templates (owner_id);

        CREATE TABLE {S}.transaction_attachments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id uuid NOT NULL REFERENCES {S}.users(id) ON DELETE RESTRICT,
            transaction_id uuid NOT NULL REFERENCES {S}.transactions(id) ON DELETE CASCADE,
            filename varchar(160) NOT NULL,
            content_type varchar(80) NOT NULL,
            size_bytes integer NOT NULL CHECK (size_bytes > 0),
            sha256 varchar(64) NOT NULL,
            content_enc bytea NOT NULL,
            key_version integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_transaction_attachments_owner_txn ON {S}.transaction_attachments (owner_id, transaction_id);

        CREATE TABLE {S}.people (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id uuid NOT NULL REFERENCES {S}.users(id) ON DELETE RESTRICT,
            name varchar(120) NOT NULL,
            email varchar(320),
            account_id uuid NOT NULL REFERENCES {S}.accounts(id) ON DELETE RESTRICT,
            archived_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_people_owner_id_account_id UNIQUE (owner_id, account_id)
        );
        CREATE INDEX ix_people_owner_id ON {S}.people (owner_id);

        CREATE TABLE {S}.alert_dismissals (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id uuid NOT NULL REFERENCES {S}.users(id) ON DELETE RESTRICT,
            alert_key varchar(200) NOT NULL,
            dismissed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_alert_dismissals_owner_id_alert_key UNIQUE (owner_id, alert_key)
        );
        ALTER TABLE {S}.investments ADD COLUMN recurring_pattern_key varchar(300);
        {_secure("transaction_templates", deletable=True)}
        {_secure("transaction_attachments", deletable=True)}
        {_secure("people", deletable=False)}
        {_secure("alert_dismissals", deletable=True)}
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.investments DROP COLUMN IF EXISTS recurring_pattern_key")
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {S}.{t}")
