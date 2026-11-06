"""Create and read transactions.

Create flow (plan section 4 hot path, M1 subset; risk evaluation joins in M2):
claim idempotency key -> insert transaction -> insert audit event -> store response
-> commit. All in ONE database transaction, so a crash at any point leaves either
everything or nothing, and a retry with the same key is always safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from ledgerguard.application.ports import ApiClient, AuditEvent, UnitOfWorkFactory
from ledgerguard.domain.idempotency import request_hash
from ledgerguard.domain.transactions import NewTransaction, Transaction


class IdempotencyKeyReusedError(Exception):
    """The key was already used by this client for a different request body."""


class TransactionNotFoundError(Exception):
    """No transaction with this id is visible to the calling client."""


@dataclass(frozen=True, slots=True)
class IdempotentResponse:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def actor_for(client: ApiClient) -> str:
    return f"api_client:{client.id}"


def transaction_view(txn: Transaction) -> dict[str, Any]:
    """Public JSON representation. Stored verbatim as the idempotent response."""
    return {
        "id": str(txn.id),
        "status": txn.status.value,
        "merchant_id": txn.merchant_id,
        "merchant_category": txn.merchant_category,
        "customer_token": txn.customer_token,
        "amount_minor": txn.amount_minor,
        "currency": txn.currency,
        "decision": txn.decision.value if txn.decision else None,
        "risk_score": str(txn.risk_score) if txn.risk_score is not None else None,
        "policy_version": txn.policy_version,
        "created_at": txn.created_at.isoformat(),
    }


class CreateTransaction:
    def __init__(self, uow_factory: UnitOfWorkFactory, idempotency_ttl: timedelta) -> None:
        self._uow_factory = uow_factory
        self._ttl = idempotency_ttl

    async def __call__(
        self,
        *,
        client: ApiClient,
        idempotency_key: str,
        new: NewTransaction,
        correlation_id: str | None,
    ) -> IdempotentResponse:
        req_hash = request_hash(new.canonical_fields())
        async with self._uow_factory() as uow:
            existing = await uow.idempotency.claim(
                client_id=client.id, key=idempotency_key, request_hash=req_hash, ttl=self._ttl
            )
            if existing is not None:
                if existing.request_hash != req_hash:
                    raise IdempotencyKeyReusedError(idempotency_key)
                return IdempotentResponse(
                    existing.response_status, existing.response_body, replayed=True
                )

            txn = await uow.transactions.add(
                client_id=client.id, new=new, correlation_id=correlation_id
            )
            await uow.audit.add(
                AuditEvent(
                    actor=actor_for(client),
                    action="transaction.created",
                    resource_type="transaction",
                    resource_id=str(txn.id),
                    correlation_id=correlation_id,
                    metadata={"idempotency_key": idempotency_key},
                )
            )
            body = transaction_view(txn)
            await uow.idempotency.complete(
                client_id=client.id, key=idempotency_key, response_status=201, response_body=body
            )
            await uow.commit()
        return IdempotentResponse(201, body, replayed=False)


class GetTransaction:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, *, client: ApiClient, transaction_id: UUID) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            txn = await uow.transactions.get_for_client(transaction_id, client.id)
        if txn is None:
            # Another client's transaction is indistinguishable from a missing one.
            raise TransactionNotFoundError(str(transaction_id))
        return transaction_view(txn)
