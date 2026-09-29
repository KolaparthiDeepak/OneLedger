"""Add "Cash Withdrawal" (ATM -> cash wallet) and "Shared with Others" transfer categories to existing ledgers.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for code, name in (("TRANSFERS_CASH", "Cash Withdrawal"), ("TRANSFERS_SHARED", "Shared with Others")):
        op.execute(
            f"""
            INSERT INTO oneledger.transaction_categories
                (id, owner_id, code, name, parent_id, default_effect, is_system, created_at, updated_at)
            SELECT gen_random_uuid(), p.owner_id, '{code}', '{name}', p.id, 'transfer', true, now(), now()
              FROM oneledger.transaction_categories p
             WHERE p.code = 'TRANSFERS'
               AND NOT EXISTS (
                   SELECT 1 FROM oneledger.transaction_categories c
                    WHERE c.owner_id = p.owner_id AND c.code = '{code}')
            """
        )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM oneledger.transaction_categories c
         WHERE c.code IN ('TRANSFERS_CASH', 'TRANSFERS_SHARED')
           AND NOT EXISTS (SELECT 1 FROM oneledger.transaction_allocations a WHERE a.category_id = c.id)
        """
    )
