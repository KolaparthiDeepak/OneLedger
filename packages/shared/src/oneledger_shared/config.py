"""Validated runtime configuration loaded from environment variables."""

from __future__ import annotations

import base64
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PREVIEW = "preview"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.LOCAL
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    default_timezone: str = "Asia/Kolkata"
    default_currency: str = "INR"

    database_url: SecretStr = SecretStr("postgresql+psycopg://oneledger_runtime@localhost:54329/oneledger")
    migration_database_url: SecretStr | None = None
    database_pool_mode: str = Field(default="session", pattern="^(session|transaction)$")

    secret_encryption_key: SecretStr = SecretStr("")
    secret_key_version: int = 1
    previous_secret_encryption_keys: SecretStr = SecretStr("")
    token_hash_key: SecretStr = SecretStr("")

    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    require_mfa: bool = False
    cookie_secure: bool = False
    allowed_origins: str = "http://localhost:3000"

    scheduler_secret: SecretStr = SecretStr("")
    bff_shared_secret: SecretStr = SecretStr("")
    job_batch_size: int = 500
    job_time_budget_seconds: int = 20
    worker_poll_seconds: float = 2.0

    ai_enabled: bool = False
    ai_provider: str = "openrouter"
    ai_model: str = ""  # empty: the provider's default model
    ai_custom_base_url: str = ""  # operator-defined OpenAI-compatible endpoint for provider "custom"
    ai_api_key: SecretStr = SecretStr("")
    ai_daily_budget: int = 50
    ai_timeout_seconds: float = 45.0
    ai_max_tool_rounds: int = 6

    log_level: str = "INFO"
    raw_retention_days: int = 30
    max_upload_bytes: int = 20 * 1024 * 1024
    max_import_rows: int = 100_000

    @field_validator("default_currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        if len(v) != 3 or not v.isalpha():
            raise ValueError("DEFAULT_CURRENCY must be an ISO 4217 code")
        return v.upper()

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production_like(self) -> bool:
        return self.app_env in (AppEnv.PRODUCTION, AppEnv.PREVIEW)

    @model_validator(mode="after")
    def _validate_security(self) -> Settings:
        problems: list[str] = []
        for name in ("secret_encryption_key", "token_hash_key"):
            raw = getattr(self, name).get_secret_value()
            if not raw:
                problems.append(f"{name.upper()} is required")
                continue
            try:
                if len(base64.urlsafe_b64decode(raw.encode())) != 32:
                    problems.append(f"{name.upper()} must be 32 bytes, urlsafe-base64 encoded")
            except ValueError:
                problems.append(f"{name.upper()} must be urlsafe-base64 encoded")
        if self.is_production_like:
            if "*" in self.allowed_origins:
                problems.append("ALLOWED_ORIGINS must not contain a wildcard")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true in preview/production")
            if not self.require_mfa:
                problems.append("REQUIRE_MFA must be true in preview/production")
            if len(self.scheduler_secret.get_secret_value()) < 32:
                problems.append("SCHEDULER_SECRET must be at least 32 characters in preview/production")
            db = self.database_url.get_secret_value()
            if "localhost" not in db and "sslmode=" not in db:
                problems.append("DATABASE_URL must specify sslmode for hosted databases")
        if problems:
            raise ValueError("Unsafe configuration: " + "; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
