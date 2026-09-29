"""Let the app remove recurring suggestions that no longer match anything (derived data only).

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON oneledger.recurring_transactions TO oneledger_app")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON oneledger.recurring_transactions FROM oneledger_app")
