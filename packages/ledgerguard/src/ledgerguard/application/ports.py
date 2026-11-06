"""Repository and Unit of Work protocols, plus the plain records they exchange.

Infrastructure (``infrastructure/db``) implements these; use cases depend only on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from ledgerguard.domain.transactions import NewTransaction, Transaction


@dataclass(frozen=True, slots=True)
class ApiClient:
    id: UUID
    tenant_id: UUID
    name: str
    status: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A completed (committed) idempotent request."""

    request_hash: str
    response_status: int
    response_body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    actor: str
    action: str
    resource_type: str
    resource_id: str
    correlation_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


class ApiClientRepository(Protocol):
    async def get_active_by_key_hash(self, key_hash: str) -> ApiClient | None: ...

    async def add(self, *, tenant_id: UUID, name: str, key_hash: str) -> ApiClient: ...


class TransactionRepository(Protocol):
    async def add(
        self, *, client_id: UUID, new: NewTransaction, correlation_id: str | None
    ) -> Transaction: ...

    async def get_for_client(self, transaction_id: UUID, client_id: UUID) -> Transaction | None: ...


class IdempotencyRepository(Protocol):
    async def claim(
        self, *, client_id: UUID, key: str, request_hash: str, ttl: timedelta
    ) -> IdempotencyRecord | None:
        """Reserve ``key`` for this request.

        Returns None when this caller now owns the key (new, or the previous record had
        expired). Otherwise returns the live record. A concurrent uncommitted claim for
        the same key blocks until that transaction ends: the primary key, not an
        application lock, serialises duplicates (plan A2).
        """
        ...

    async def complete(
        self, *, client_id: UUID, key: str, response_status: int, response_body: dict[str, Any]
    ) -> None: ...

    async def delete_expired(self, *, limit: int) -> int: ...


class AuditRepository(Protocol):
    async def add(self, event: AuditEvent) -> None: ...


class UnitOfWork(Protocol):
    """One database transaction. Nothing is committed unless ``commit()`` is called."""

    @property
    def api_clients(self) -> ApiClientRepository: ...

    @property
    def transactions(self) -> TransactionRepository: ...

    @property
    def idempotency(self) -> IdempotencyRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    async def commit(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
