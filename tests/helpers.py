"""Shared test helpers."""

from __future__ import annotations

from typing import Any

from ledgerguard.config import Settings


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the developer's .env and environment-specific defaults."""
    values: dict[str, Any] = {
        "env": "test",
        "database_url": "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        "redis_url": "redis://127.0.0.1:1/0",
        "kafka_bootstrap_servers": "127.0.0.1:1",
        "otel_enabled": False,
        "log_format": "json",
        "readiness_timeout_s": 1.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
