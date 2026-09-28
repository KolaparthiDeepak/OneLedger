"""Alembic environment. Uses MIGRATION_DATABASE_URL (never the restricted runtime credential)."""

from __future__ import annotations

import os
from logging.config import fileConfig

import oneledger_db.models  # noqa: F401  (registers tables)
from alembic import context
from oneledger_db.base import SCHEMA, Base
from sqlalchemy import create_engine, text

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required to run migrations")
    return url


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # Views and the alembic table are managed by hand-written revisions.
    return not (type_ == "table" and name in ("income_records", "expense_records", "alembic_version"))


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        # Serialize concurrent migration runs.
        connection.execute(text("SELECT pg_advisory_lock(727274001)"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.execute(text("SELECT pg_advisory_unlock(727274001)"))
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
