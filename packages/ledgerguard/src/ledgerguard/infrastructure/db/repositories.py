"""SQLAlchemy implementations of the repository protocols in ``application.ports``."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Interval, delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ledgerguard.application.ports import ApiClient, AuditEvent, IdempotencyRecord
from ledgerguard.domain.transactions import (
    Decision,
    NewTransaction,
    Transaction,
    TransactionStatus,
)
from ledgerguard.infrastructure.db.models import (
    ApiClientRow,
    AuditEventRow,
    IdempotencyRecordRow,
    TransactionRow,
)


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
        self, *, client_id: UUID, new: NewTransaction, correlation_id: str | None
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
                status=TransactionStatus.CREATED.value,
                correlation_id=correlation_id,
            )
            .returning(TransactionRow)
        )
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always yields a row
        return _to_transaction(row)

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
    ) -> IdempotencyRecord | None:
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
        ).returning(table.key)
        if await self._session.scalar(stmt) is not None:
            return None

        # READ COMMITTED: this new statement sees the row the other request committed.
        row = await self._session.scalar(
            select(table).where(table.client_id == client_id, table.key == key)
        )
        if row is None or row.response_status is None or row.response_body is None:
            # Unreachable in practice: completion is committed with the claim.
            raise RuntimeError(f"idempotency record {key!r} vanished or is incomplete")
        return IdempotencyRecord(
            request_hash=row.request_hash,
            response_status=row.response_status,
            response_body=row.response_body,
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
