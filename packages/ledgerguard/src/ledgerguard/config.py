"""Typed application settings, loaded from environment variables (prefix ``LG_``) and ``.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "test", "ci", "staging", "production"] = "local"
    service_name: str = "ledgerguard-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # No default: the database location must always be explicit.
    # Runtime connections use the least-privilege role (ledgerguard_app).
    database_url: str = Field(min_length=1)
    # Schema owner, for migrations and admin scripts. Falls back to database_url.
    migration_database_url: str | None = None
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    # Idempotency-Key retention (plan A3). After expiry a reused key is a new request.
    idempotency_ttl_s: int = Field(default=24 * 3600, gt=0)

    @property
    def owner_database_url(self) -> str:
        return self.migration_database_url or self.database_url

    redis_url: str = "redis://127.0.0.1:6379/0"
    kafka_bootstrap_servers: str = "127.0.0.1:19092"

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://127.0.0.1:4318"

    # Per-dependency timeout for /readyz checks, in seconds.
    readiness_timeout_s: float = Field(default=2.0, gt=0)

    # Used by /readyz to compare the DB schema revision against the code's head.
    alembic_ini_path: Path = Path("migrations/alembic.ini")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # required values come from the environment / .env
