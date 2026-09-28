"""Let the app delete tags (labels only; transactions are never deleted this way).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON oneledger.transaction_tags TO oneledger_app")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON oneledger.transaction_tags FROM oneledger_app")
