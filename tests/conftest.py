"""Shared fixtures. Integration tests use a real PostgreSQL database (never SQLite).

TEST_DATABASE_URL        runtime role URL (restricted, RLS enforced)   default: from .env with db oneledger_test
TEST_MIGRATION_DATABASE_URL  owner URL used to migrate/truncate
"""

from __future__ import annotations

import base64
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.split("#")[0].strip()
    return env


_DOT = _dotenv()
RUNTIME_URL = os.environ.get("TEST_DATABASE_URL") or re.sub(
    r"/oneledger$", "/oneledger_test", _DOT.get("DATABASE_URL", "")
)
MIGRATION_URL = os.environ.get("TEST_MIGRATION_DATABASE_URL") or re.sub(
    r"/oneledger$", "/oneledger_test", _DOT.get("MIGRATION_DATABASE_URL", "")
)


def _key() -> str:
    return base64.urlsafe_b64encode(b"k" * 32).decode()


os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": RUNTIME_URL,
        "MIGRATION_DATABASE_URL": MIGRATION_URL,
        "SECRET_ENCRYPTION_KEY": _key(),
        "TOKEN_HASH_KEY": base64.urlsafe_b64encode(b"t" * 32).decode(),
        "SCHEDULER_SECRET": "s" * 40,
        "BFF_SHARED_SECRET": "b" * 40,
        "AI_ENABLED": "false",
        "AI_API_KEY": "",
        "AI_PROVIDER": "openrouter",
        "AI_MODEL": "",
        "REQUIRE_MFA": "false",
        "LOG_LEVEL": "WARNING",
    }
)

OWNER_PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def migrated() -> str:
    if not RUNTIME_URL or not MIGRATION_URL:
        pytest.skip("PostgreSQL test database not configured")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    base = MIGRATION_URL.rsplit("/", 1)[0] + "/postgres"
    eng = create_engine(base, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS oneledger_test WITH (FORCE)"))
        c.execute(text("CREATE DATABASE oneledger_test"))
    eng.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "infrastructure/migrations"))
    command.upgrade(cfg, "head")
    return MIGRATION_URL


@pytest.fixture()
def clean_db(migrated: str) -> Iterator[None]:
    from sqlalchemy import create_engine, text

    eng = create_engine(migrated)
    with eng.begin() as c:
        tables = [
            r[0]
            for r in c.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='oneledger' AND tablename <> 'alembic_version'")
            )
        ]
        c.execute(text("TRUNCATE " + ", ".join(f"oneledger.{t}" for t in tables) + " CASCADE"))
    eng.dispose()
    yield


@pytest.fixture()
def ctx(clean_db: None):  # type: ignore[no-untyped-def]
    from oneledger_api.context import get_context
    from oneledger_shared.config import reset_settings_cache

    reset_settings_cache()
    get_context.cache_clear()
    return get_context()


def make_owner(ctx, email: str = "owner@example.com") -> uuid.UUID:  # type: ignore[no-untyped-def]
    from oneledger_api.cli import create_owner

    create_owner(email, "Owner", "Asia/Kolkata", "INR", OWNER_PASSWORD)
    from oneledger_db.session import owner_session
    from sqlalchemy import text

    with owner_session(ctx.sessions, None) as db:
        return db.execute(text("SELECT id FROM oneledger.auth_user_by_email(:e)"), {"e": email}).scalar_one()


@pytest.fixture()
def client(ctx):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from oneledger_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


class Api:
    """Tiny authenticated test client wrapper."""

    def __init__(self, client, token: str) -> None:  # type: ignore[no-untyped-def]
        self.c = client
        self.h = {"Authorization": f"Bearer {token}"}

    def get(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.c.get(f"/api/v1{path}", headers=self.h, **kw)

    def post(self, path: str, json=None, **kw):  # type: ignore[no-untyped-def]
        return self.c.post(f"/api/v1{path}", headers={**self.h, **kw.pop("headers", {})}, json=json, **kw)

    def put(self, path: str, json=None, **kw):  # type: ignore[no-untyped-def]
        return self.c.put(f"/api/v1{path}", headers=self.h, json=json, **kw)

    def patch(self, path: str, json=None, **kw):  # type: ignore[no-untyped-def]
        return self.c.patch(f"/api/v1{path}", headers=self.h, json=json, **kw)

    def delete(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.c.delete(f"/api/v1{path}", headers=self.h, **kw)

    def ok(self, resp, status: int = 200):  # type: ignore[no-untyped-def]
        assert resp.status_code == status, resp.text
        return resp.json()


def login(client, email: str = "owner@example.com") -> Api:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/auth/login", json={"email": email, "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    return Api(client, r.json()["session_token"])


@pytest.fixture()
def api(ctx, client) -> Api:  # type: ignore[no-untyped-def]
    make_owner(ctx)
    return login(client)
