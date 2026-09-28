"""AI auto-categorisation setting; OpenRouter as the default provider.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column("auto_categorize", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="oneledger",
    )
    op.alter_column("ai_settings", "provider", server_default="openrouter", schema="oneledger")
    op.alter_column("ai_settings", "model", server_default="anthropic/claude-opus-5", schema="oneledger")


def downgrade() -> None:
    op.alter_column("ai_settings", "model", server_default="claude-opus-5", schema="oneledger")
    op.alter_column("ai_settings", "provider", server_default="anthropic", schema="oneledger")
    op.drop_column("ai_settings", "auto_categorize", schema="oneledger")
