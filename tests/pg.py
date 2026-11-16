"""Real-Postgres helpers for integration tests (Testcontainers, same image as compose)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from apps.api.main import create_app
from ledgerguard.application.api_clients import IssuedApiKey, register_api_client
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.migrations import alembic_config
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.infrastructure.risk_assets import load_model_file
from tests.helpers import make_settings

POSTGRES_IMAGE = "pgvector/pgvector:0.8.0-pg16"
REDIS_IMAGE = "redis:7.4-alpine"
REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "migrations" / "alembic.ini"
MODEL_ARTIFACT = REPO_ROOT / "models" / "lr-v1.json"
APP_ROLE_PASSWORD = "app_test"  # noqa: S105 - throwaway test container


@dataclass(frozen=True)
class Database:
    owner_url: str  # schema owner: migrations, fixtures, assertions
    app_url: str  # least-privilege runtime role, as the API uses in every environment


def _ipv4(url: str) -> str:
    # "localhost" costs ~2 s per connection on Windows (ADR-009).
    parsed = make_url(url)
    if parsed.host == "localhost":
        parsed = parsed.set(host="127.0.0.1")
    return parsed.render_as_string(hide_password=False)


def migrate(owner_url: str, revision: str = "head", *, down: bool = False) -> None:
    cfg = alembic_config(ALEMBIC_INI, owner_url)
    cfg.attributes["configure_logger"] = False
    (command.downgrade if down else command.upgrade)(cfg, revision)


async def owner_query(db: Database, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    engine = create_async_engine(db.owner_url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return [tuple(row) for row in result.all()] if result.returns_rows else []
    finally:
        await engine.dispose()


async def new_client(db: Database, name: str = "test") -> IssuedApiKey:
    engine = create_async_engine(db.owner_url)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        return await register_api_client(factory, name=name)
    finally:
        await engine.dispose()


async def register_model(
    db: Database, path: Path = MODEL_ARTIFACT, *, activate: bool = True
) -> None:
    """Register (and activate) a model artifact, as scripts/register_model.py does."""
    model = load_model_file(path)
    engine = create_async_engine(db.owner_url)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        async with factory() as uow:
            await uow.models.register(
                model, artifact_path=path.name, trained_at=datetime.now(UTC), activate=activate
            )
            await uow.commit()
    finally:
        await engine.dispose()


@contextmanager
def migrated_database(*, with_model: bool = True) -> Iterator[Database]:
    """Fresh Postgres at head; by default model v1 is registered and active."""
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as pg:
        owner_url = _ipv4(pg.get_connection_url())
        migrate(owner_url)
        app_url = (
            make_url(owner_url)
            .set(username="ledgerguard_app", password=APP_ROLE_PASSWORD)
            .render_as_string(hide_password=False)
        )
        db = Database(owner_url=owner_url, app_url=app_url)
        asyncio.run(
            owner_query(db, f"ALTER ROLE ledgerguard_app LOGIN PASSWORD '{APP_ROLE_PASSWORD}'")
        )
        if with_model:
            asyncio.run(register_model(db))
        yield db


@contextmanager
def redis_server() -> Iterator[str]:
    """A throwaway Redis (same image as compose); yields its URL."""
    with RedisContainer(REDIS_IMAGE) as redis:
        host = redis.get_container_host_ip().replace("localhost", "127.0.0.1")
        yield f"redis://{host}:{redis.get_exposed_port(6379)}/0"


@asynccontextmanager
async def api_client(db: Database, **overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    """In-process API (real lifespan) connected as the app role."""
    settings = make_settings(
        database_url=db.app_url,
        migration_database_url=db.owner_url,
        alembic_ini_path=ALEMBIC_INI,
        risk_assets_dir=REPO_ROOT,
        **overrides,
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client
