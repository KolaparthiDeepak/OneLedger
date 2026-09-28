"""Remember why automatic AI categorisation stopped, so the owner can be told.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE oneledger.ai_settings ADD COLUMN auto_error_code varchar(60), "
        "ADD COLUMN auto_error_message varchar(300), ADD COLUMN auto_error_at timestamptz"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE oneledger.ai_settings DROP COLUMN auto_error_code, "
        "DROP COLUMN auto_error_message, DROP COLUMN auto_error_at"
    )
