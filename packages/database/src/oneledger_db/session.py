"""Engine and session factory with transaction-scoped owner context for row-level security.

Every transaction begins with ``set_config('app.owner_id', <owner>, true)``; the setting is
transaction-local so it never leaks across pooled connections. RLS policies compare each
row's ``owner_id`` to that setting, and the runtime role has no ``BYPASSRLS``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

OWNER_KEY = "owner_id"


def make_engine(url: str, *, pool_mode: str = "session", pool_size: int = 5) -> Engine:
    connect_args: dict[str, object] = {"application_name": "oneledger"}
    if pool_mode == "transaction":
        # Transaction poolers (Supavisor/pgbouncer) cannot keep server-side prepared statements.
        connect_args["prepare_threshold"] = None
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=5,
        pool_recycle=300,
        connect_args=connect_args,
        future=True,
    )


@event.listens_for(Session, "after_begin")
def _apply_owner_context(session: Session, transaction: object, connection: object) -> None:
    owner = session.info.get(OWNER_KEY)
    connection.execute(  # type: ignore[attr-defined]
        text("SELECT set_config('app.owner_id', :owner, true)"), {"owner": str(owner) if owner else ""}
    )


@lru_cache(maxsize=4)
def _factory(url: str, pool_mode: str) -> sessionmaker[Session]:
    return sessionmaker(make_engine(url, pool_mode=pool_mode), expire_on_commit=False, autoflush=True)


def session_factory(url: str, pool_mode: str = "session") -> sessionmaker[Session]:
    return _factory(url, pool_mode)


@contextmanager
def owner_session(factory: sessionmaker[Session], owner_id: uuid.UUID | None) -> Iterator[Session]:
    """Session bound to one owner. Commits on success, rolls back on error."""
    session = factory()
    session.info[OWNER_KEY] = owner_id
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def set_owner(session: Session, owner_id: uuid.UUID) -> None:
    """Switch owner context inside an open session (e.g. after login lookup)."""
    session.info[OWNER_KEY] = owner_id
    session.execute(text("SELECT set_config('app.owner_id', :owner, true)"), {"owner": str(owner_id)})
