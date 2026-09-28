"""Idempotency-Key handling for side-effecting POST operations."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from oneledger_db.models import IdempotencyKey
from oneledger_shared.errors import ConflictError
from sqlalchemy import select
from sqlalchemy.orm import Session


def idempotent(
    db: Session,
    owner_id: uuid.UUID,
    key: str | None,
    route: str,
    body: dict[str, Any],
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run ``fn`` once per (owner, key, route). Reusing a key with a different body returns 409."""
    if not key:
        return fn()
    request_hash = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    existing = db.scalars(
        select(IdempotencyKey)
        .where(IdempotencyKey.owner_id == owner_id, IdempotencyKey.key == key, IdempotencyKey.route == route)
        .with_for_update()
    ).first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ConflictError(
                "This Idempotency-Key was used with a different request.", code="IDEMPOTENCY_KEY_REUSED"
            )
        return existing.response_body
    result = fn()
    db.add(
        IdempotencyKey(
            owner_id=owner_id,
            key=key[:128],
            route=route,
            request_hash=request_hash,
            response_status=200,
            response_body=json.loads(json.dumps(result, default=str)),
        )
    )
    db.flush()
    return result
