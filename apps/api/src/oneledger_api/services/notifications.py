"""Phone alerts through ntfy (https://ntfy.sh): bills due and the in-app alerts, pushed to your phone.

The scheduler calls :func:`check_and_send` about once a minute for each owner who turned it on.
Each alert is sent once (``notification_log``); a bill is sent again on the day it is due, at the
highest priority so the ntfy app can ring through Do Not Disturb. Nothing is sent during quiet hours
(22:00-07:00 in your timezone) when those are on; it goes out when they end.

The topic is the only thing protecting the messages on a public ntfy server, so it is long, random,
and stored encrypted. With "show amounts" off the message says only that something needs attention.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx
from oneledger_db.models import NotificationLog, NotificationSettings, User, UserSecret
from oneledger_domain.money import format_inr
from oneledger_shared.errors import ServiceUnavailable, ValidationFailed
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..context import AppContext

log = logging.getLogger("oneledger.notifications")

TOPIC_KIND = "ntfy_topic"
DEFAULT_SERVER = "https://ntfy.sh"
QUIET_START, QUIET_END = 22, 7  # local hours
MAX_PER_CHECK = 5
TIMEOUT_SECONDS = 8.0
# Tests replace this with an httpx.MockTransport; nothing else should.
TRANSPORT: httpx.BaseTransport | None = None

_TOPIC = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_BILL_KEY = re.compile(r"^bill:[a-z]+:[^:]+:(\d{4}-\d{2}-\d{2})$")


def get_settings(db: Session, owner_id: uuid.UUID) -> NotificationSettings:
    row = db.get(NotificationSettings, owner_id)
    if row is None:
        row = NotificationSettings(owner_id=owner_id)
        db.add(row)
        db.flush()
        db.refresh(row)
    return row


def _topic_row(db: Session, owner_id: uuid.UUID) -> UserSecret | None:
    return db.scalars(select(UserSecret).where(UserSecret.owner_id == owner_id, UserSecret.kind == TOPIC_KIND)).first()


def topic(ctx: AppContext, db: Session, owner_id: uuid.UUID) -> str | None:
    row = _topic_row(db, owner_id)
    if row is None:
        return None
    return ctx.box.decrypt_str(row.ciphertext, associated_data=f"{owner_id}:{TOPIC_KIND}".encode())


def new_topic(ctx: AppContext, db: Session, owner_id: uuid.UUID) -> str:
    """Create or replace the topic. The old one stops receiving anything at once."""
    value = f"oneledger-{secrets.token_urlsafe(24)}"
    blob = ctx.box.encrypt_str(value, associated_data=f"{owner_id}:{TOPIC_KIND}".encode())
    row = _topic_row(db, owner_id)
    if row is None:
        db.add(
            UserSecret(
                owner_id=owner_id,
                kind=TOPIC_KIND,
                ciphertext=blob,
                key_version=ctx.box.version,
                masked_suffix=value[-4:],
            )
        )
    else:
        row.ciphertext, row.key_version, row.masked_suffix = blob, ctx.box.version, value[-4:]
    db.flush()
    return value


def clean_server_url(url: str) -> str:
    url = url.strip().rstrip("/") or DEFAULT_SERVER
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.query or parts.fragment:
        raise ValidationFailed("Enter the ntfy server address, like https://ntfy.sh.", code="INVALID_NTFY_SERVER")
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise ValidationFailed("Use https for an ntfy server on the internet.", code="INVALID_NTFY_SERVER")
    return url


def settings_out(ctx: AppContext, db: Session, owner_id: uuid.UUID) -> dict[str, Any]:
    s = get_settings(db, owner_id)
    t = topic(ctx, db, owner_id)
    return {
        "enabled": s.enabled,
        "server_url": s.server_url,
        "topic": t,
        "subscribe_url": f"{s.server_url}/{t}" if t else None,
        "bills": s.bills,
        "alerts": s.alerts,
        "show_amounts": s.show_amounts,
        "quiet_hours": s.quiet_hours,
        "last_sent_at": s.last_sent_at,
        "last_error": s.last_error,
    }


# --- Messages ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Message:
    key: str
    title: str
    body: str
    priority: int  # ntfy 1-5; 5 = urgent (long vibration, can bypass Do Not Disturb)
    tags: tuple[str, ...]
    click: str | None


_AMOUNT = re.compile(r"-?\d+\.\d{2}\b")


def _money(text: str) -> str:
    """Server text carries plain decimals ("28000.00 due 05 Oct."); show them as ₹28,000.00."""

    def one(m: re.Match[str]) -> str:
        try:
            return "₹" + format_inr(Decimal(m.group(0)))
        except InvalidOperation:
            return m.group(0)

    return _AMOUNT.sub(one, text)


def messages_for(alerts: list[dict[str, Any]], s: NotificationSettings, today: date, app_url: str) -> list[Message]:
    out: list[Message] = []
    for a in alerts:
        key, severity = a["key"], a.get("severity", "low")
        bill = _BILL_KEY.match(key)
        if (bill and not s.bills) or (not bill and not s.alerts):
            continue
        click = f"{app_url.rstrip('/')}{a['href']}" if a.get("href", "").startswith("/") else None
        title, body = a["title"], _money(a.get("detail", ""))
        if bill and bill.group(1) == today.isoformat():
            key, title, priority = f"{key}:today", title.replace("Due soon", "Due today", 1), 5
        else:
            priority = {"high": 5, "medium": 4}.get(severity, 3)
        if not s.show_amounts:
            title = "OneLedger: a bill is due" if bill else "OneLedger: something needs attention"
            body = "Open OneLedger to see it."
        tags = ("rotating_light",) if priority == 5 else ("money_with_wings",) if bill else ("bell",)
        out.append(Message(key, title, body, priority, tags, click))
    return out


# --- Sending -------------------------------------------------------------------------------------


def send(server_url: str, topic_value: str, m: Message) -> None:
    """Publish one message (ntfy JSON publishing). Raises ServiceUnavailable on any failure."""
    if not _TOPIC.match(topic_value):
        raise ServiceUnavailable("The phone alert topic is not valid; create a new one.", code="NTFY_TOPIC_INVALID")
    payload: dict[str, Any] = {
        "topic": topic_value,
        "title": m.title[:200],
        "message": m.body[:1000] or m.title[:200],
        "priority": m.priority,
        "tags": list(m.tags),
    }
    if m.click:
        payload["click"] = m.click
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=TRANSPORT, follow_redirects=False) as client:
            r = client.post(server_url, json=payload)
    except httpx.HTTPError as e:
        raise ServiceUnavailable(f"Couldn't reach {urlsplit(server_url).netloc}.", code="NTFY_UNREACHABLE") from e
    if r.status_code >= 400:
        raise ServiceUnavailable(
            f"{urlsplit(server_url).netloc} refused the alert (HTTP {r.status_code}).", code="NTFY_REJECTED"
        )


def send_test(ctx: AppContext, db: Session, owner_id: uuid.UUID) -> None:
    s = get_settings(db, owner_id)
    t = topic(ctx, db, owner_id) or new_topic(ctx, db, owner_id)
    m = Message(
        "test",
        "OneLedger test alert",
        "Phone alerts work. Bills and alerts will arrive like this.",
        5,
        ("white_check_mark",),
        ctx.settings.app_base_url,
    )
    try:
        send(s.server_url, t, m)
    except ServiceUnavailable as e:
        s.last_error = str(e)[:300]
        raise
    s.last_error, s.last_sent_at = None, datetime.now(UTC)


def in_quiet_hours(local_hour: int) -> bool:
    return local_hour >= QUIET_START or local_hour < QUIET_END


def check_and_send(ctx: AppContext, db: Session, owner_id: uuid.UUID, now: datetime | None = None) -> int:
    """Send alerts not sent before. Returns how many went out. Safe to call as often as you like."""
    from zoneinfo import ZoneInfo

    from .insights import alerts

    s = db.get(NotificationSettings, owner_id)
    if s is None or not s.enabled:
        return 0
    t = topic(ctx, db, owner_id)
    user = db.get(User, owner_id)
    if t is None or user is None:
        return 0
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(user.timezone))
    if s.quiet_hours and in_quiet_hours(local.hour):
        return 0
    found = alerts(db, owner_id, local.date(), user.base_currency)
    sent_keys = set(db.scalars(select(NotificationLog.alert_key).where(NotificationLog.owner_id == owner_id)))
    due = [m for m in messages_for(found, s, local.date(), ctx.settings.app_base_url) if m.key not in sent_keys]
    sent = 0
    for m in sorted(due, key=lambda m: -m.priority)[:MAX_PER_CHECK]:
        claimed = db.execute(
            insert(NotificationLog)
            .values(owner_id=owner_id, alert_key=m.key[:220], priority=m.priority)
            .on_conflict_do_nothing(constraint="uq_notification_log_owner_id_alert_key")
            .returning(NotificationLog.id)
        ).scalar()
        if claimed is None:
            continue  # another tick sent it
        try:
            send(s.server_url, t, m)
        except ServiceUnavailable as e:
            db.execute(delete(NotificationLog).where(NotificationLog.id == claimed))
            s.last_error = str(e)[:300]
            log.warning("phone alert failed", extra={"event": "ntfy_failed", "code": e.code})
            break  # try again next tick
        s.last_error, s.last_sent_at = None, datetime.now(UTC)
        sent += 1
    return sent
