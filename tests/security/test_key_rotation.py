"""Encryption key rotation re-encrypts stored secrets; afterwards the old key can be retired."""

from __future__ import annotations

import base64

from oneledger_api.context import get_context
from oneledger_shared.config import reset_settings_cache

from tests.integration.helpers import account, csv_bytes, upload

KEY = "sk-ant-rotation-" + "z" * 40


def test_rotate_encryption_key(api, monkeypatch):
    api.ok(api.put("/ai/key", json={"api_key": KEY}))
    acct = account(api, "Bank")
    upload(api, acct, csv_bytes(["02/08/2026,SHOP,,10.00,,1.00"]))
    old = base64.urlsafe_b64encode(b"k" * 32).decode()
    new = base64.urlsafe_b64encode(b"n" * 32).decode()
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", new)
    monkeypatch.setenv("SECRET_KEY_VERSION", "2")
    monkeypatch.setenv("PREVIOUS_SECRET_ENCRYPTION_KEYS", f"1:{old}")
    reset_settings_cache()
    get_context.cache_clear()
    from oneledger_api.cli import rotate_encryption_key

    rotate_encryption_key()
    # Retire the old key entirely: everything must still decrypt.
    monkeypatch.setenv("PREVIOUS_SECRET_ENCRYPTION_KEYS", "")
    reset_settings_cache()
    get_context.cache_clear()
    ctx = get_context()
    from oneledger_api.services.ai import _resolve_key
    from oneledger_api.services.ai_providers import provider_info
    from oneledger_db.models import ImportFile
    from oneledger_db.session import owner_session
    from sqlalchemy import select

    owner = api.ok(api.get("/me"))["id"]
    with owner_session(ctx.sessions, owner) as db:
        assert _resolve_key(ctx, db, owner, provider_info(ctx, "openrouter")) == KEY
        f = db.scalars(select(ImportFile)).one()
        assert ctx.box.key_version_of(f.content_enc) == 2
        assert b"SHOP" in ctx.box.decrypt(f.content_enc, associated_data=f.sha256.encode())
