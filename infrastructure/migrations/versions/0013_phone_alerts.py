"""Phone alerts through ntfy: per-owner settings and a log of what was sent (each alert once).

The ntfy topic is a secret (anyone who knows it can read the alerts), so it lives encrypted in
``user_secrets`` with kind ``ntfy_topic``, not here.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "oneledger"
OWNER_EXPR = "NULLIF(current_setting('app.owner_id', true), '')::uuid"
TABLES = ("notification_settings", "notification_log")


def _secure(table: str) -> str:
    fq = f"{S}.{table}"
    return f"""
        REVOKE ALL ON {fq} FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE, DELETE ON {fq} TO oneledger_app;
        ALTER TABLE {fq} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {fq} FORCE ROW LEVEL SECURITY;
        CREATE POLICY owner_isolation ON {fq} FOR ALL TO oneledger_app
            USING (owner_id = {OWNER_EXPR}) WITH CHECK (owner_id = {OWNER_EXPR});
    """


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {S}.notification_settings (
            owner_id uuid PRIMARY KEY REFERENCES {S}.users(id) ON DELETE RESTRICT,
            enabled boolean NOT NULL DEFAULT false,
            server_url varchar(300) NOT NULL DEFAULT 'https://ntfy.sh',
            bills boolean NOT NULL DEFAULT true,
            alerts boolean NOT NULL DEFAULT true,
            show_amounts boolean NOT NULL DEFAULT true,
            quiet_hours boolean NOT NULL DEFAULT true,
            last_sent_at timestamptz,
            last_error varchar(300),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE {S}.notification_log (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id uuid NOT NULL REFERENCES {S}.users(id) ON DELETE RESTRICT,
            alert_key varchar(220) NOT NULL,
            priority smallint NOT NULL,
            sent_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_notification_log_owner_id_alert_key UNIQUE (owner_id, alert_key)
        );
        CREATE INDEX ix_notification_log_owner_id ON {S}.notification_log (owner_id);
        {_secure("notification_settings")}
        {_secure("notification_log")}
        """
    )


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {S}.{t}")
