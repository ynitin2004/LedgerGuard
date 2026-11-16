"""Create (and evaluate) and read transactions.

Hot path (plan section 4; outbox joins in M3), all in ONE database transaction:
claim idempotency key -> read customer history -> velocity (Redis, Postgres fallback)
-> features -> rules + model -> policy -> insert transaction + risk decision + audit
event -> store response -> commit. Then, outside the transaction, record the payment
in the Redis velocity window (best effort; M3 makes it reliable via the outbox).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from ledgerguard.application.ports import (
    ApiClient,
    AuditEvent,
    CustomerHistory,
    RiskDecisionRecord,
    RiskEngineSource,
    StoredRiskDecision,
    UnitOfWorkFactory,
    VelocityStore,
)
from ledgerguard.domain.idempotency import request_hash
from ledgerguard.domain.transactions import NewTransaction, Transaction
from ledgerguard.events.schemas import Envelope, TransactionEvaluatedV1
from ledgerguard.observability.logging import get_logger
from ledgerguard.risk.engine import RiskOutcome
from ledgerguard.risk.features import (
    VELOCITY_1H,
    PaymentAttempt,
    VelocityCounts,
    compute_features,
    velocity_from_history,
)

log = get_logger(__name__)

# Upper bound on history rows read per evaluation (30-day window). Keeps the hot path
# bounded for extreme customers; features are computed over at most this many rows.
HISTORY_LIMIT = 1000


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


def format_score(value: float | Decimal | None) -> str | None:
    """Scores are exact 5-decimal strings in JSON, identical before and after storage."""
    return None if value is None else f"{Decimal(str(value)):.5f}"


def transaction_view(txn: Transaction, reason_codes: list[str]) -> dict[str, Any]:
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
        "risk_score": format_score(txn.risk_score),
        "reason_codes": reason_codes,
        "policy_version": txn.policy_version,
        "created_at": txn.created_at.isoformat(),
    }


def decision_view(d: StoredRiskDecision) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "transaction_id": str(d.transaction_id),
        "decision": d.decision,
        "score": format_score(d.score),
        "model_score": format_score(d.model_score),
        "reason_codes": d.reason_codes,
        "rules_version": d.rules_version,
        "model_version": d.model_version,
        "thresholds_version": d.thresholds_version,
        "policy_version": d.policy_version,
        "feature_snapshot": d.feature_snapshot,
        "evidence_refs": d.evidence_refs,
        "created_at": d.created_at.isoformat(),
    }


def evaluated_event(
    client: ApiClient,
    txn: Transaction,
    outcome: RiskOutcome,
    decision_id: UUID,
    correlation_id: str | None,
) -> Envelope:
    payload = TransactionEvaluatedV1(
        transaction_id=txn.id,
        client_id=client.id,
        customer_token=txn.customer_token,
        merchant_id=txn.merchant_id,
        merchant_category=txn.merchant_category,
        amount_minor=txn.amount_minor,
        currency=txn.currency,
        status=txn.status.value,
        decision=outcome.decision.value,
        risk_score=f"{Decimal(str(outcome.score)):.5f}",
        reason_codes=outcome.reason_codes,
        policy_version=outcome.policy_version,
        risk_decision_id=decision_id,
        created_at=txn.created_at,
    )
    return Envelope(
        event_id=uuid4(),
        event_type="transaction.evaluated",
        schema_version=1,
        # Kafka key: per-transaction ordering (plan s.4).
        aggregate_id=str(txn.id),
        payload=payload.model_dump(mode="json"),
        correlation_id=correlation_id,
    )


class CreateTransaction:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        idempotency_ttl: timedelta,
        engines: RiskEngineSource,
        velocity: VelocityStore,
    ) -> None:
        self._uow_factory = uow_factory
        self._ttl = idempotency_ttl
        self._engines = engines
        self._velocity = velocity

    async def __call__(
        self,
        *,
        client: ApiClient,
        idempotency_key: str,
        new: NewTransaction,
        correlation_id: str | None,
    ) -> IdempotentResponse:
        # Fail before touching the database when no engine can decide (503).
        engine = await self._engines.current()
        req_hash = request_hash(new.canonical_fields())
        async with self._uow_factory() as uow:
            claim = await uow.idempotency.claim(
                client_id=client.id, key=idempotency_key, request_hash=req_hash, ttl=self._ttl
            )
            if claim.existing is not None:
                if claim.existing.request_hash != req_hash:
                    raise IdempotencyKeyReusedError(idempotency_key)
                return IdempotentResponse(
                    claim.existing.response_status, claim.existing.response_body, replayed=True
                )
            assert claim.now is not None  # noqa: S101 - set whenever existing is None

            history = await uow.transactions.recent_history(
                client_id=client.id,
                customer_token=new.customer_token,
                now=claim.now,
                limit=HISTORY_LIMIT,
            )
            velocity = await self._velocity_counts(client.id, new.customer_token, history)
            attempt = PaymentAttempt(
                new.amount_minor, new.currency, new.merchant_id, new.merchant_category
            )
            features = compute_features(attempt, history.items, history.now, velocity)
            outcome = engine.evaluate(features)

            txn = await uow.transactions.add(
                client_id=client.id, new=new, correlation_id=correlation_id, outcome=outcome
            )
            decision_id = uuid4()
            await uow.risk_decisions.add(
                RiskDecisionRecord(
                    id=decision_id,
                    transaction_id=txn.id,
                    outcome=outcome,
                    feature_snapshot=features,
                    # Plan A11: stable evidence IDs the investigator (M4) may cite.
                    evidence_refs=[f"{decision_id}:{code}" for code in outcome.reason_codes],
                )
            )
            await uow.audit.add(
                AuditEvent(
                    actor=actor_for(client),
                    action="transaction.created",
                    resource_type="transaction",
                    resource_id=str(txn.id),
                    correlation_id=correlation_id,
                    metadata={
                        "idempotency_key": idempotency_key,
                        "decision": outcome.decision.value,
                        "risk_decision_id": str(decision_id),
                        "policy_version": outcome.policy_version,
                    },
                )
            )
            # Transactional outbox (M3): the event commits or rolls back with the payment,
            # so there is never a payment without its event or an event without a payment.
            await uow.outbox.add(
                evaluated_event(client, txn, outcome, decision_id, correlation_id),
                aggregate_type="transaction",
            )
            body = transaction_view(txn, outcome.reason_codes)
            await uow.idempotency.complete(
                client_id=client.id, key=idempotency_key, response_status=201, response_body=body
            )
            await uow.commit()

        await self._record_velocity(client.id, new.customer_token, str(txn.id), txn)
        log.info(
            "transaction.evaluated",
            transaction_id=str(txn.id),
            decision=outcome.decision.value,
            score=outcome.score,
            reasons=outcome.reason_codes,
        )
        return IdempotentResponse(201, body, replayed=False)

    async def _velocity_counts(
        self, client_id: UUID, customer_token: str, history: CustomerHistory
    ) -> VelocityCounts:
        """Redis windows when complete; otherwise the same counts from Postgres history.

        A miss also rebuilds the Redis window from that history (plan A8: Redis down or
        cold means slower, not different).
        """
        try:
            counts = await self._velocity.counts(client_id, customer_token, history.now)
        except Exception as exc:  # any Redis failure degrades to Postgres
            log.warning("velocity.redis_unavailable", error=type(exc).__name__)
            return velocity_from_history(history.items, history.now)
        if counts is not None:
            return counts
        fallback = velocity_from_history(history.items, history.now)
        entries = [
            (h.transaction_id, h.created_at)
            for h in history.items
            if h.transaction_id and h.created_at > history.now - VELOCITY_1H
        ]
        try:
            await self._velocity.rebuild(client_id, customer_token, entries)
        except Exception as exc:
            log.warning("velocity.rebuild_failed", error=type(exc).__name__)
        return fallback

    async def _record_velocity(
        self, client_id: UUID, customer_token: str, transaction_id: str, txn: Transaction
    ) -> None:
        try:
            await self._velocity.record(client_id, customer_token, transaction_id, txn.created_at)
        except Exception as exc:
            # The window would now under-count; drop it so the next read rebuilds it
            # from Postgres. If Redis is fully down, reads fall back anyway.
            log.warning("velocity.record_failed", error=type(exc).__name__)
            try:
                await self._velocity.invalidate(client_id, customer_token)
            except Exception:
                log.warning("velocity.invalidate_failed", customer_window="stale until TTL")


class GetTransaction:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, *, client: ApiClient, transaction_id: UUID) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            txn = await uow.transactions.get_for_client(transaction_id, client.id)
            decisions = await uow.risk_decisions.list_for_transaction(transaction_id) if txn else []
        if txn is None:
            # Another client's transaction is indistinguishable from a missing one.
            raise TransactionNotFoundError(str(transaction_id))
        latest = decisions[-1].reason_codes if decisions else []
        return transaction_view(txn, latest)


class ListDecisions:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, *, client: ApiClient, transaction_id: UUID) -> list[dict[str, Any]]:
        async with self._uow_factory() as uow:
            txn = await uow.transactions.get_for_client(transaction_id, client.id)
            if txn is None:
                raise TransactionNotFoundError(str(transaction_id))
            decisions = await uow.risk_decisions.list_for_transaction(transaction_id)
        return [decision_view(d) for d in decisions]
