"""Process-wide application context (settings, DB factory, crypto)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from oneledger_db.session import session_factory
from oneledger_shared.config import Settings, get_settings
from oneledger_shared.crypto import SecretBox, keyed_hash, parse_key_ring
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    sessions: sessionmaker[Session]
    box: SecretBox

    def token_hash(self, token: str) -> str:
        return keyed_hash(self.settings.token_hash_key.get_secret_value(), token)


def build_context(settings: Settings) -> AppContext:
    box = SecretBox(
        settings.secret_encryption_key.get_secret_value(),
        settings.secret_key_version,
        parse_key_ring(settings.previous_secret_encryption_keys.get_secret_value()),
    )
    factory = session_factory(settings.database_url.get_secret_value(), settings.database_pool_mode)
    return AppContext(settings=settings, sessions=factory, box=box)


@lru_cache(maxsize=1)
def get_context() -> AppContext:
    return build_context(get_settings())
