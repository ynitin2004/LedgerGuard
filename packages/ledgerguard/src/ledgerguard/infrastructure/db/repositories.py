"""SQLAlchemy implementations of the repository protocols in ``application.ports``."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Interval, delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ledgerguard.application.ports import (
    ApiClient,
    AuditEvent,
    ClaimResult,
    CustomerHistory,
    IdempotencyRecord,
    OutboxBacklog,
    OutboxMessage,
    RiskDecisionRecord,
    StoredRiskDecision,
)
from ledgerguard.domain.transactions import (
    Decision,
    NewTransaction,
    Transaction,
    TransactionStatus,
)
from ledgerguard.events.schemas import Envelope
from ledgerguard.infrastructure.db.models import (
    ApiClientRow,
    AuditEventRow,
    ConsumerInboxRow,
    IdempotencyRecordRow,
    ModelRegistryRow,
    OutboxEventRow,
    RiskDecisionRow,
    TransactionRow,
)
from ledgerguard.risk.engine import RiskOutcome
from ledgerguard.risk.features import HISTORY_WINDOW, HistoryItem
from ledgerguard.risk.model import LogisticModel


def _to_client(row: ApiClientRow) -> ApiClient:
    return ApiClient(id=row.id, tenant_id=row.tenant_id, name=row.name, status=row.status)


def _to_transaction(row: TransactionRow) -> Transaction:
    return Transaction(
        id=row.id,
        client_id=row.client_id,
        merchant_id=row.merchant_id,
        merchant_category=row.merchant_category,
        customer_token=row.customer_token,
        amount_minor=row.amount_minor,
        currency=row.currency,
        status=TransactionStatus(row.status),
        created_at=row.created_at,
        correlation_id=row.correlation_id,
        decision=Decision(row.decision) if row.decision else None,
        risk_score=row.risk_score,
        policy_version=row.policy_version,
        rules_version=row.rules_version,
        model_version=row.model_version,
    )


class SqlApiClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_key_hash(self, key_hash: str) -> ApiClient | None:
        row = await self._session.scalar(
            select(ApiClientRow).where(
                ApiClientRow.key_hash == key_hash, ApiClientRow.status == "active"
            )
        )
        return _to_client(row) if row else None

    async def add(self, *, tenant_id: UUID, name: str, key_hash: str) -> ApiClient:
        row = await self._session.scalar(
            pg_insert(ApiClientRow)
            .values(tenant_id=tenant_id, name=name, key_hash=key_hash)
            .returning(ApiClientRow)
        )
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always yields a row
        return _to_client(row)


class SqlTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        client_id: UUID,
        new: NewTransaction,
        correlation_id: str | None,
        outcome: RiskOutcome,
    ) -> Transaction:
        row = await self._session.scalar(
            pg_insert(TransactionRow)
            .values(
                client_id=client_id,
                merchant_id=new.merchant_id,
                merchant_category=new.merchant_category,
                customer_token=new.customer_token,
                amount_minor=new.amount_minor,
                currency=new.currency,
                # Evaluated in the same transaction, so the row is born with its decision.
                status=TransactionStatus.EVALUATED.value,
                decision=outcome.decision.value,
                risk_score=Decimal(str(outcome.score)),
                policy_version=outcome.policy_version,
                rules_version=outcome.rules_version,
                model_version=outcome.model_version,
                correlation_id=correlation_id,
            )
            .returning(TransactionRow)
        )
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always yields a row
        return _to_transaction(row)

    async def recent_history(
        self, *, client_id: UUID, customer_token: str, now: datetime, limit: int
    ) -> CustomerHistory:
        t = TransactionRow
        rows = await self._session.execute(
            select(
                t.id,
                t.created_at,
                t.amount_minor,
                t.currency,
                t.merchant_id,
                t.merchant_category,
                t.status,
            )
            .where(
                t.client_id == client_id,
                t.customer_token == customer_token,
                t.created_at > now - HISTORY_WINDOW,
                t.created_at <= now,
            )
            .order_by(t.created_at.desc())
            .limit(limit)
        )
        items = [
            HistoryItem(
                created_at=r.created_at,
                amount_minor=r.amount_minor,
                currency=r.currency,
                merchant_id=r.merchant_id,
                merchant_category=r.merchant_category,
                status=r.status,
                transaction_id=str(r.id),
            )
            for r in rows
        ]
        items.reverse()  # chronological
        return CustomerHistory(now=now, items=items)

    async def get_for_client(self, transaction_id: UUID, client_id: UUID) -> Transaction | None:
        row = await self._session.scalar(
            select(TransactionRow).where(
                TransactionRow.id == transaction_id, TransactionRow.client_id == client_id
            )
        )
        return _to_transaction(row) if row else None


class SqlIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self, *, client_id: UUID, key: str, request_hash: str, ttl: timedelta
    ) -> ClaimResult:
        table = IdempotencyRecordRow
        insert_stmt = pg_insert(table).values(
            client_id=client_id,
            key=key,
            request_hash=request_hash,
            expires_at=func.now() + literal(ttl, Interval()),
        )
        # One statement covers all three cases:
        #  - no row: insert, we own the key;
        #  - expired row: take it over (plan A3: an expired key is treated as new);
        #  - live row, possibly uncommitted by a concurrent request: Postgres waits for
        #    that transaction, then the WHERE fails and nothing is returned.
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[table.client_id, table.key],
            set_={
                "request_hash": insert_stmt.excluded.request_hash,
                "response_status": None,
                "response_body": None,
                "created_at": func.now(),
                "expires_at": insert_stmt.excluded.expires_at,
            },
            where=table.expires_at <= func.now(),
        ).returning(table.key, func.now())
        claimed = (await self._session.execute(stmt)).first()
        if claimed is not None:
            return ClaimResult(now=claimed[1], existing=None)

        # READ COMMITTED: this new statement sees the row the other request committed.
        row = await self._session.scalar(
            select(table).where(table.client_id == client_id, table.key == key)
        )
        if row is None or row.response_status is None or row.response_body is None:
            # Unreachable in practice: completion is committed with the claim.
            raise RuntimeError(f"idempotency record {key!r} vanished or is incomplete")
        return ClaimResult(
            now=None,
            existing=IdempotencyRecord(
                request_hash=row.request_hash,
                response_status=row.response_status,
                response_body=row.response_body,
            ),
        )

    async def complete(
        self, *, client_id: UUID, key: str, response_status: int, response_body: dict[str, Any]
    ) -> None:
        await self._session.execute(
            update(IdempotencyRecordRow)
            .where(IdempotencyRecordRow.client_id == client_id, IdempotencyRecordRow.key == key)
            .values(response_status=response_status, response_body=response_body)
            .execution_options(synchronize_session=False)
        )

    async def delete_expired(self, *, limit: int) -> int:
        table = IdempotencyRecordRow
        batch = (
            select(table.client_id, table.key)
            .where(table.expires_at <= func.now())
            .order_by(table.expires_at)
            .limit(limit)
            # Rows being taken over by a concurrent claim are skipped, not waited on.
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(
            delete(table)
            .where(tuple_(table.client_id, table.key).in_(batch))
            .returning(table.key)
            .execution_options(synchronize_session=False)
        )
        return len(result.all())


class SqlRiskDecisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: RiskDecisionRecord) -> None:
        o = record.outcome
        await self._session.execute(
            pg_insert(RiskDecisionRow).values(
                id=record.id,
                transaction_id=record.transaction_id,
                rules_version=o.rules_version,
                model_version=o.model_version,
                thresholds_version=o.thresholds_version,
                policy_version=o.policy_version,
                decision=o.decision.value,
                score=Decimal(str(o.score)),
                model_score=Decimal(str(o.model_score)),
                reason_codes=o.reason_codes,
                feature_snapshot=record.feature_snapshot,
                evidence_refs=record.evidence_refs,
            )
        )

    async def list_for_transaction(self, transaction_id: UUID) -> list[StoredRiskDecision]:
        rows = await self._session.scalars(
            select(RiskDecisionRow)
            .where(RiskDecisionRow.transaction_id == transaction_id)
            .order_by(RiskDecisionRow.created_at, RiskDecisionRow.id)
        )
        return [
            StoredRiskDecision(
                id=r.id,
                transaction_id=r.transaction_id,
                decision=r.decision,
                score=r.score,
                model_score=r.model_score,
                reason_codes=r.reason_codes,
                rules_version=r.rules_version,
                model_version=r.model_version,
                thresholds_version=r.thresholds_version,
                policy_version=r.policy_version,
                feature_snapshot=r.feature_snapshot,
                evidence_refs=r.evidence_refs,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def transaction_ids(self, limit: int | None) -> list[UUID]:
        stmt = (
            select(RiskDecisionRow.transaction_id)
            .distinct()
            .order_by(RiskDecisionRow.transaction_id)
        )
        if limit:
            stmt = stmt.limit(limit)
        return list((await self._session.scalars(stmt)).all())


class SqlModelRegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self) -> LogisticModel | None:
        row = await self._session.scalar(
            select(ModelRegistryRow).where(ModelRegistryRow.active.is_(True))
        )
        return _to_model(row) if row else None

    async def get(self, model_version: str) -> LogisticModel | None:
        row = await self._session.get(ModelRegistryRow, model_version)
        return _to_model(row) if row else None

    async def register(
        self, model: LogisticModel, *, artifact_path: str, trained_at: datetime, activate: bool
    ) -> None:
        """Insert a new version (versions are immutable) and optionally make it active."""
        if activate:
            await self._deactivate_all()
        await self._session.execute(
            pg_insert(ModelRegistryRow).values(
                model_version=model.version,
                algo=model.algo,
                trained_at=trained_at,
                metrics=model.metrics,
                artifact_path=artifact_path,
                artifact=model.model_dump(mode="json"),
                artifact_sha256=model.sha256(),
                active=activate,
            )
        )

    async def activate(self, model_version: str) -> None:
        await self._deactivate_all()
        await self._session.execute(
            update(ModelRegistryRow)
            .where(ModelRegistryRow.model_version == model_version)
            .values(active=True)
            .execution_options(synchronize_session=False)
        )

    async def _deactivate_all(self) -> None:
        # Flip the old row first: the partial unique index allows one active row.
        await self._session.execute(
            update(ModelRegistryRow)
            .where(ModelRegistryRow.active.is_(True))
            .values(active=False)
            .execution_options(synchronize_session=False)
        )


def _to_model(row: ModelRegistryRow) -> LogisticModel:
    model = LogisticModel.model_validate(row.artifact)
    if model.sha256() != row.artifact_sha256:
        raise ValueError(f"model {row.model_version}: artifact does not match its sha256")
    return model


class SqlAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: AuditEvent) -> None:
        await self._session.execute(
            pg_insert(AuditEventRow).values(
                actor=event.actor,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                correlation_id=event.correlation_id,
                metadata_=event.metadata,
            )
        )


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, envelope: Envelope, *, aggregate_type: str) -> None:
        await self._session.execute(
            pg_insert(OutboxEventRow).values(
                event_id=envelope.event_id,
                aggregate_type=aggregate_type,
                aggregate_id=envelope.aggregate_id,
                event_type=envelope.event_type,
                schema_version=envelope.schema_version,
                payload=envelope.payload,
                correlation_id=envelope.correlation_id,
            )
        )

    async def claim_batch(self, limit: int) -> list[OutboxMessage]:
        t = OutboxEventRow
        rows = await self._session.scalars(
            select(t)
            .where(t.published_at.is_(None))
            .order_by(t.created_at, t.event_id)
            .limit(limit)
            # Several publishers can run: each claims a disjoint set of rows.
            .with_for_update(skip_locked=True)
        )
        return [
            OutboxMessage(
                envelope=Envelope(
                    event_id=r.event_id,
                    event_type=r.event_type,
                    schema_version=r.schema_version,
                    aggregate_id=r.aggregate_id,
                    payload=r.payload,
                    correlation_id=r.correlation_id,
                ),
                attempt_count=r.attempt_count,
            )
            for r in rows
        ]

    async def mark_published(self, event_ids: list[UUID]) -> None:
        if not event_ids:
            return
        await self._session.execute(
            update(OutboxEventRow)
            .where(OutboxEventRow.event_id.in_(event_ids))
            .values(published_at=func.now(), attempt_count=OutboxEventRow.attempt_count + 1)
            .execution_options(synchronize_session=False)
        )

    async def mark_failed(self, event_id: UUID, error: str) -> None:
        await self._session.execute(
            update(OutboxEventRow)
            .where(OutboxEventRow.event_id == event_id)
            .values(attempt_count=OutboxEventRow.attempt_count + 1, last_error=error[:1000])
            .execution_options(synchronize_session=False)
        )

    async def backlog(self) -> OutboxBacklog:
        t = OutboxEventRow
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.extract("epoch", func.now() - func.min(t.created_at)),
                ).where(t.published_at.is_(None))
            )
        ).one()
        oldest = float(row[1]) if row[1] is not None else None
        return OutboxBacklog(unpublished=int(row[0]), oldest_age_s=oldest)


class SqlInboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, consumer_name: str, event_id: UUID) -> bool:
        inserted = await self._session.scalar(
            pg_insert(ConsumerInboxRow)
            .values(consumer_name=consumer_name, event_id=event_id)
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(ConsumerInboxRow.event_id)
        )
        return inserted is not None

    async def record_many(self, consumer_name: str, event_ids: list[UUID]) -> set[UUID]:
        if not event_ids:
            return set()
        rows = await self._session.scalars(
            pg_insert(ConsumerInboxRow)
            .values([{"consumer_name": consumer_name, "event_id": e} for e in event_ids])
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(ConsumerInboxRow.event_id)
        )
        return set(rows.all())
