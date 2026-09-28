"""Creating a person's own ledger (from the command line or an invite link)."""

from __future__ import annotations

import uuid

from oneledger_db.models import User
from oneledger_db.session import set_owner
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..context import AppContext
from ..security.auth import hash_password
from .categories import seed_owner_categories

MIN_PASSWORD = 12


def email_taken(db: Session, email: str) -> bool:
    return (
        db.execute(text("SELECT id FROM oneledger.auth_user_by_email(:e)"), {"e": email.strip().lower()}).first()
        is not None
    )


def provision_user(
    ctx: AppContext, db: Session, *, email: str, name: str, password: str, timezone: str, currency: str
) -> User:
    """Create the account with default categories and AI settings; the caller commits.

    The new person's rows are scoped to their own id from the start (row-level security), so their
    ledger is separate from everyone else's on this server.
    """
    owner_id = uuid.uuid4()
    set_owner(db, owner_id)
    user = User(
        id=owner_id,
        email=email.strip().lower(),
        password_hash=hash_password(password),
        display_name=name.strip() or email.split("@")[0],
        timezone=timezone,
        base_currency=currency.upper(),
    )
    db.add(user)
    db.flush()
    from .ai import get_settings_row

    get_settings_row(ctx, db, owner_id)
    seed_owner_categories(db, owner_id)
    return user
