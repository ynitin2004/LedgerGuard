"""FastAPI application factory and composition root.

Run locally:  uv run python -m uvicorn --factory apps.api.main:create_app --reload

There is deliberately no module-level ``app``: importing this module must not read
settings or open connections (tests and tooling import it freely).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from apps.api.routers import health
from ledgerguard import __version__
from ledgerguard.config import Settings, get_settings
from ledgerguard.infrastructure.db.engine import create_engine
from ledgerguard.infrastructure.db.migrations import expected_head
from ledgerguard.infrastructure.health import (
    Probe,
    kafka_probe,
    postgres_probe,
    redis_probe,
)
from ledgerguard.observability.correlation import CorrelationIdMiddleware
from ledgerguard.observability.logging import configure_logging, get_logger
from ledgerguard.observability.tracing import configure_telemetry

log = get_logger(__name__)


def create_app(settings: Settings | None = None, probes: list[Probe] | None = None) -> FastAPI:
    """Build the app.

    ``probes`` lets tests inject readiness probes; when given, the lifespan opens no
    real connections.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        if probes is not None:
            app.state.probes = probes
            yield
            return

        engine = create_engine(settings)
        redis = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.readiness_timeout_s,
            socket_timeout=settings.readiness_timeout_s,
        )
        app.state.engine = engine
        app.state.redis = redis
        app.state.probes = [
            postgres_probe(engine, expected_head(settings.alembic_ini_path)),
            redis_probe(redis),
            kafka_probe(settings.kafka_bootstrap_servers, settings.readiness_timeout_s),
        ]
        log.info("api.startup", env=settings.env, version=__version__)
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            log.info("api.shutdown")

    app = FastAPI(title="LedgerGuard AI", version=__version__, lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    configure_telemetry(app, settings)
    return app
