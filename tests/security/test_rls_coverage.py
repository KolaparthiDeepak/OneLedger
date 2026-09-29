"""Every table that holds someone's data enforces row-level security (new tables included)."""

from __future__ import annotations

from sqlalchemy import create_engine, text


def test_every_owner_table_forces_row_level_security(migrated):
    eng = create_engine(migrated)
    with eng.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT t.relname, t.relrowsecurity, t.relforcerowsecurity,
                       EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = t.oid) AS has_policy
                  FROM pg_class t
                  JOIN pg_namespace n ON n.oid = t.relnamespace
                 WHERE n.nspname = 'oneledger' AND t.relkind = 'r'
                   AND EXISTS (SELECT 1 FROM information_schema.columns col
                                WHERE col.table_schema = 'oneledger' AND col.table_name = t.relname
                                  AND col.column_name = 'owner_id')
                """
            )
        ).all()
    eng.dispose()
    unprotected = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity and r.has_policy)]
    assert rows and unprotected == []
