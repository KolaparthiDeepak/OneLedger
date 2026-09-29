"""Phone alerts (ntfy): settings, a new topic, and a test alert. Session sign-in only: the topic is a
secret, so API tokens (AI clients, MCP) can't read it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import AdminAuth
from ..services import notifications as n
from ..services.audit import audit

router = APIRouter(tags=["notifications"])


class PhoneAlertsIn(BaseModel):
    enabled: bool | None = None
    server_url: str | None = Field(default=None, max_length=300)
    bills: bool | None = None
    alerts: bool | None = None
    show_amounts: bool | None = None
    quiet_hours: bool | None = None


@router.get("/notifications/settings")
def get_settings(a: AdminAuth) -> dict[str, Any]:
    out = n.settings_out(a.ctx, a.db, a.owner_id)
    a.commit()
    return out


@router.put("/notifications/settings")
def update_settings(body: PhoneAlertsIn, a: AdminAuth) -> dict[str, Any]:
    s = n.get_settings(a.db, a.owner_id)
    changed = body.model_dump(exclude_none=True)
    if "server_url" in changed:
        changed["server_url"] = n.clean_server_url(changed["server_url"])
    for k, v in changed.items():
        setattr(s, k, v)
    if s.enabled and n.topic(a.ctx, a.db, a.owner_id) is None:
        n.new_topic(a.ctx, a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "notifications.update", "notification_settings", a.owner_id, sorted(changed))
    out = n.settings_out(a.ctx, a.db, a.owner_id)
    a.commit()
    return out


@router.post("/notifications/topic")
def rotate_topic(a: AdminAuth) -> dict[str, Any]:
    """A new private topic. The phone must subscribe to it again; the old one gets nothing more."""
    n.new_topic(a.ctx, a.db, a.owner_id)
    audit(a.db, a.owner_id, a.actor, "notifications.topic", "notification_settings", a.owner_id, ["topic"])
    out = n.settings_out(a.ctx, a.db, a.owner_id)
    a.commit()
    return out


@router.post("/notifications/test")
def send_test(a: AdminAuth) -> dict[str, Any]:
    try:
        n.send_test(a.ctx, a.db, a.owner_id)
    finally:
        a.commit()  # keep the recorded error (or success) either way
    return n.settings_out(a.ctx, a.db, a.owner_id)
