"""Audit trail and ledger revision bookkeeping.

Audit rows hold actor, action, entity id and *names* of changed fields only -- never amounts,
descriptions or secrets. Protected before/after financial facts live in transaction_revisions.
"""

from __future__ import annotations

import uuid
from typing import Any

from oneledger_db.models import AuditLog, TransactionRevision
from oneledger_shared.logging import request_id_var
from sqlalchemy import text
from sqlalchemy.orm import Session


def audit(
    db: Session,
    owner_id: uuid.UUID,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    changed_fields: list[str] | None = None,
    revision_id: uuid.UUID | None = None,
) -> None:
    db.add(
        AuditLog(
            owner_id=owner_id,
            actor=actor[:40],
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changed_fields=changed_fields or [],
            revision_id=revision_id,
            correlation_id=request_id_var.get(),
        )
    )


def bump_ledger_revision(db: Session, owner_id: uuid.UUID) -> int:
    """Increment the owner's ledger revision; call inside the same DB transaction as the change."""
    return int(
        db.execute(
            text(
                "INSERT INTO oneledger.ledger_state (owner_id, ledger_revision) VALUES (:o, 1) "
                "ON CONFLICT (owner_id) DO UPDATE SET ledger_revision = oneledger.ledger_state.ledger_revision + 1, "
                "updated_at = now() RETURNING ledger_revision"
            ),
            {"o": owner_id},
        ).scalar_one()
    )


def current_ledger_revision(db: Session, owner_id: uuid.UUID) -> int:
    value = db.execute(
        text("SELECT ledger_revision FROM oneledger.ledger_state WHERE owner_id = :o"), {"o": owner_id}
    ).scalar()
    return int(value or 0)


def record_revision(
    db: Session,
    owner_id: uuid.UUID,
    transaction_id: uuid.UUID,
    revision: int,
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str,
    actor: str,
) -> uuid.UUID:
    rev = TransactionRevision(
        owner_id=owner_id,
        transaction_id=transaction_id,
        revision=revision,
        before=before,
        after=after,
        reason=reason[:200],
        actor=actor[:40],
    )
    db.add(rev)
    db.flush()
    return rev.id
