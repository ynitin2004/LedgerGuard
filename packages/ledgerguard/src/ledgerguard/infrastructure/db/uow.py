"""SQLAlchemy Unit of Work: one session = one database transaction."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ledgerguard.infrastructure.db.repositories import (
    SqlApiClientRepository,
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlTransactionRepository,
)


class SqlAlchemyUnitOfWork:
    """Single use. Rolls back on exit unless ``commit()`` was called (never autocommits)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self._committed = False
        self.api_clients = SqlApiClientRepository(self._session)
        self.transactions = SqlTransactionRepository(self._session)
        self.idempotency = SqlIdempotencyRepository(self._session)
        self.audit = SqlAuditRepository(self._session)

    async def commit(self) -> None:
        await self._session.commit()
        self._committed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self._session.rollback()
        finally:
            await self._session.close()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)
