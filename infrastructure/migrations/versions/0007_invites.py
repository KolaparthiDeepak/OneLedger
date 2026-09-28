"""Invite links: let someone create their own, separate ledger on this server.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "oneledger"
OWNER_EXPR = "NULLIF(current_setting('app.owner_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {S}.invites (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id uuid NOT NULL REFERENCES {S}.users(id) ON DELETE RESTRICT,
            email varchar(320),
            note varchar(120),
            token_hash varchar(64) NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            used_by uuid REFERENCES {S}.users(id) ON DELETE RESTRICT,
            revoked_at timestamptz
        );
        CREATE INDEX ix_invites_owner_id ON {S}.invites (owner_id);
        REVOKE ALL ON {S}.invites FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE ON {S}.invites TO oneledger_app;
        ALTER TABLE {S}.invites ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {S}.invites FORCE ROW LEVEL SECURITY;
        CREATE POLICY owner_isolation ON {S}.invites FOR ALL TO oneledger_app
            USING (owner_id = {OWNER_EXPR}) WITH CHECK (owner_id = {OWNER_EXPR});

        -- The person opening a link is not signed in, so these two run with the definer's rights
        -- and only ever look up or consume the single invite whose token hash is given.
        CREATE FUNCTION {S}.invite_lookup(p_hash text)
        RETURNS TABLE (id uuid, email text, inviter_name text, expires_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT i.id, i.email, u.display_name, i.expires_at
            FROM {S}.invites i JOIN {S}.users u ON u.id = i.owner_id
           WHERE i.token_hash = p_hash AND i.used_at IS NULL AND i.revoked_at IS NULL
             AND i.expires_at > now() AND u.is_owner_enabled
        $$;
        CREATE FUNCTION {S}.invite_consume(p_hash text, p_user uuid)
        RETURNS uuid
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          UPDATE {S}.invites SET used_at = now(), used_by = p_user
           WHERE token_hash = p_hash AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()
          RETURNING id
        $$;
        -- Lets the person who sent an invite see which email joined with it, and nothing else.
        CREATE FUNCTION {S}.invite_joined_email(p_invite uuid)
        RETURNS TABLE (email text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, {S} AS $$
          SELECT u.email FROM {S}.invites i JOIN {S}.users u ON u.id = i.used_by
           WHERE i.id = p_invite AND i.owner_id = {OWNER_EXPR}
        $$;
        REVOKE ALL ON FUNCTION {S}.invite_joined_email(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION {S}.invite_joined_email(uuid) TO oneledger_app;
        REVOKE ALL ON FUNCTION {S}.invite_lookup(text) FROM PUBLIC;
        REVOKE ALL ON FUNCTION {S}.invite_consume(text, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION {S}.invite_lookup(text) TO oneledger_app;
        GRANT EXECUTE ON FUNCTION {S}.invite_consume(text, uuid) TO oneledger_app;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {S}.invite_joined_email(uuid)")
    op.execute(f"DROP FUNCTION IF EXISTS {S}.invite_consume(text, uuid)")
    op.execute(f"DROP FUNCTION IF EXISTS {S}.invite_lookup(text)")
    op.execute(f"DROP TABLE IF EXISTS {S}.invites")
