"""Async engine and session factory.

Driver: asyncpg. psycopg's async mode does not support the Windows Proactor event
loop, which is the default on Windows dev machines (docs/DECISIONS.md, ADR-008).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ledgerguard.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False keeps objects readable after an explicit commit.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
