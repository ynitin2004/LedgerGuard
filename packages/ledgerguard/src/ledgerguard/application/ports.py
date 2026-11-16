"""Repository and Unit of Work protocols, plus the plain records they exchange.

Infrastructure (``infrastructure/db``) implements these; use cases depend only on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from ledgerguard.domain.transactions import NewTransaction, Transaction
from ledgerguard.events.schemas import Envelope
from ledgerguard.risk.engine import RiskEngine, RiskOutcome
from ledgerguard.risk.features import FeatureSnapshot, HistoryItem, VelocityCounts
from ledgerguard.risk.model import LogisticModel


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
class ClaimResult:
    """Outcome of claiming an idempotency key.

    Exactly one is set: ``now`` (this request owns the key; the transaction's database
    timestamp, returned by the claim itself to save a round trip) or ``existing``.
    """

    now: datetime | None
    existing: IdempotencyRecord | None


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


@dataclass(frozen=True, slots=True)
class CustomerHistory:
    """A customer's recent transactions plus the database clock they were read at.

    ``now`` is the transaction timestamp (Postgres ``now()``), so feature windows, the
    new row's ``created_at`` and Redis window bounds all use one clock.
    """

    now: datetime
    items: list[HistoryItem]


@dataclass(frozen=True, slots=True)
class RiskDecisionRecord:
    id: UUID
    transaction_id: UUID
    outcome: RiskOutcome
    feature_snapshot: FeatureSnapshot
    evidence_refs: list[str]


@dataclass(frozen=True, slots=True)
class StoredRiskDecision:
    id: UUID
    transaction_id: UUID
    decision: str
    score: Decimal
    model_score: Decimal
    reason_codes: list[str]
    rules_version: str
    model_version: str
    thresholds_version: str
    policy_version: str
    feature_snapshot: dict[str, Any]
    evidence_refs: list[str]
    created_at: datetime


class TransactionRepository(Protocol):
    async def add(
        self,
        *,
        client_id: UUID,
        new: NewTransaction,
        correlation_id: str | None,
        outcome: RiskOutcome,
    ) -> Transaction: ...

    async def get_for_client(self, transaction_id: UUID, client_id: UUID) -> Transaction | None: ...

    async def recent_history(
        self, *, client_id: UUID, customer_token: str, now: datetime, limit: int
    ) -> CustomerHistory: ...


class RiskDecisionRepository(Protocol):
    async def add(self, record: RiskDecisionRecord) -> None: ...

    async def list_for_transaction(self, transaction_id: UUID) -> list[StoredRiskDecision]: ...

    async def transaction_ids(self, limit: int | None) -> list[UUID]: ...


class ModelRegistryRepository(Protocol):
    async def get_active(self) -> LogisticModel | None: ...

    async def get(self, model_version: str) -> LogisticModel | None: ...

    async def register(
        self, model: LogisticModel, *, artifact_path: str, trained_at: datetime, activate: bool
    ) -> None: ...

    async def activate(self, model_version: str) -> None: ...


class VelocityStore(Protocol):
    """Per-customer payment-time windows (Redis). Every method may raise on outage."""

    async def counts(
        self, client_id: UUID, customer_token: str, now: datetime
    ) -> VelocityCounts | None:
        """Window counts, or None when the window is not known to be complete."""
        ...

    async def rebuild(
        self,
        client_id: UUID,
        customer_token: str,
        entries: list[tuple[str, datetime]],
    ) -> None: ...

    async def record(
        self, client_id: UUID, customer_token: str, transaction_id: str, at: datetime
    ) -> None: ...

    async def invalidate(self, client_id: UUID, customer_token: str) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    envelope: Envelope
    attempt_count: int


@dataclass(frozen=True, slots=True)
class OutboxBacklog:
    unpublished: int
    oldest_age_s: float | None


class OutboxRepository(Protocol):
    async def add(self, envelope: Envelope, *, aggregate_type: str) -> None: ...

    async def claim_batch(self, limit: int) -> list[OutboxMessage]:
        """Oldest unpublished rows, locked FOR UPDATE SKIP LOCKED (parallel publishers)."""
        ...

    async def mark_published(self, event_ids: list[UUID]) -> None: ...

    async def mark_failed(self, event_id: UUID, error: str) -> None: ...

    async def backlog(self) -> OutboxBacklog: ...


class InboxRepository(Protocol):
    async def record(self, consumer_name: str, event_id: UUID) -> bool:
        """True the first time this consumer sees the event; False for a duplicate."""
        ...

    async def record_many(self, consumer_name: str, event_ids: list[UUID]) -> set[UUID]:
        """Record a batch in one statement; returns the ids seen for the first time."""
        ...


class RiskEngineSource(Protocol):
    async def current(self) -> RiskEngine:
        """The engine for new decisions; raises RiskEngineUnavailableError if none."""
        ...


class RiskEngineUnavailableError(Exception):
    """No usable rules/model/policy triple (e.g. no active model registered)."""


class IdempotencyRepository(Protocol):
    async def claim(
        self, *, client_id: UUID, key: str, request_hash: str, ttl: timedelta
    ) -> ClaimResult:
        """Reserve ``key`` for this request.

        ``now`` is set when this caller owns the key (new, or the previous record had
        expired); otherwise ``existing`` holds the live record. A concurrent uncommitted claim for
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

    @property
    def risk_decisions(self) -> RiskDecisionRepository: ...

    @property
    def models(self) -> ModelRegistryRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    @property
    def inbox(self) -> InboxRepository: ...

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
