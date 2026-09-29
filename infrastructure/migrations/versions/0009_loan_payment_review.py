"""Review item kind for bank debits that look like a loan EMI but need confirmation.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'POSSIBLE_DUPLICATE','TRANSFER_SUGGESTION','UNMATCHED_TRANSFER','RECONCILIATION','SOURCE_REVISION','CATEGORIZATION'"


def upgrade() -> None:
    op.execute("ALTER TABLE oneledger.review_items DROP CONSTRAINT ck_review_items_review_kind")
    op.execute(
        "ALTER TABLE oneledger.review_items ADD CONSTRAINT ck_review_items_review_kind "
        f"CHECK (kind IN ({_OLD},'LOAN_PAYMENT_SUGGESTION'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM oneledger.review_items WHERE kind = 'LOAN_PAYMENT_SUGGESTION'")
    op.execute("ALTER TABLE oneledger.review_items DROP CONSTRAINT ck_review_items_review_kind")
    op.execute(
        f"ALTER TABLE oneledger.review_items ADD CONSTRAINT ck_review_items_review_kind CHECK (kind IN ({_OLD}))"
    )
