"""Allow imports to be deleted (their transactions removed from the ledger).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE = ["UPLOADED", "PARSING", "NEEDS_MAPPING", "VALIDATING", "PREVIEW_READY", "CONFIRMED", "COMMITTING",
         "COMPLETED", "FAILED", "CANCELLED"]


def _constraint(states: list[str]) -> None:
    op.execute("ALTER TABLE oneledger.imports DROP CONSTRAINT IF EXISTS ck_imports_import_state")
    values = ", ".join(f"'{s}'" for s in states)
    op.execute(f"ALTER TABLE oneledger.imports ADD CONSTRAINT ck_imports_import_state CHECK (state IN ({values}))")


def upgrade() -> None:
    _constraint([*_BASE, "DELETED"])


def downgrade() -> None:
    op.execute("UPDATE oneledger.imports SET state = 'CANCELLED' WHERE state = 'DELETED'")
    _constraint(_BASE)
