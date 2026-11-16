"""FastAPI application factory and composition root.

Run locally:  uv run python -m uvicorn --factory apps.api.main:create_app --reload

There is deliberately no module-level ``app``: importing this module must not read
settings or open connections (tests and tooling import it freely).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from apps.api.problems import install_problem_handlers
from apps.api.routers import health, transactions
from ledgerguard import __version__
from ledgerguard.application.api_clients import CachingAuthenticator
from ledgerguard.application.transactions import (
    CreateTransaction,
    GetTransaction,
    ListDecisions,
)
from ledgerguard.config import Settings, get_settings
from ledgerguard.infrastructure.db.engine import create_engine, create_session_factory
from ledgerguard.infrastructure.db.migrations import expected_head
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.infrastructure.health import (
    Probe,
    kafka_probe,
    postgres_probe,
    redis_probe,
    risk_engine_probe,
)
from ledgerguard.infrastructure.redis import RedisRateLimiter, RedisVelocityStore, create_redis
from ledgerguard.infrastructure.risk_engine_provider import RiskEngineProvider
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
        uow_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        # Two Redis clients: the hot path fails fast (no retries, tight timeout); the
        # readiness probe tolerates a slower answer.
        hot_redis = create_redis(settings.redis_url, settings.redis_timeout_s)
        probe_redis = create_redis(settings.redis_url, settings.readiness_timeout_s)
        engines = RiskEngineProvider(
            uow_factory, settings.risk_assets_dir, settings.rules_version, settings.policy_version
        )
        app.state.uow_factory = uow_factory
        app.state.authenticator = CachingAuthenticator(uow_factory, settings.api_key_cache_ttl_s)
        app.state.rate_limiter = RedisRateLimiter(hot_redis, settings.rate_limit_per_second)
        app.state.create_transaction = CreateTransaction(
            uow_factory,
            timedelta(seconds=settings.idempotency_ttl_s),
            engines,
            RedisVelocityStore(hot_redis),
        )
        app.state.get_transaction = GetTransaction(uow_factory)
        app.state.list_decisions = ListDecisions(uow_factory)
        app.state.engine = engine
        app.state.probes = [
            postgres_probe(engine, expected_head(settings.alembic_ini_path)),
            risk_engine_probe(engines),
            redis_probe(probe_redis),
            kafka_probe(settings.kafka_bootstrap_servers, settings.readiness_timeout_s),
        ]
        log.info("api.startup", env=settings.env, version=__version__)
        try:
            yield
        finally:
            await hot_redis.aclose()
            await probe_redis.aclose()
            await engine.dispose()
            log.info("api.shutdown")

    app = FastAPI(title="LedgerGuard AI", version=__version__, lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health.router)
    app.include_router(transactions.router)
    configure_telemetry(app, settings)
    return app
