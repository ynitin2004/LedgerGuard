"""Outbox publishing and idempotent event consumption (plan s.6, M3).

Delivery is at-least-once end to end:
- the publisher marks rows published only after the broker acknowledged them, in the
  same transaction that locked them. A crash after sending but before commit leaves
  the rows unpublished, so they are sent again (duplicates are possible);
- each consumer records ``(consumer_name, event_id)`` in its inbox in the same database
  transaction as its side effects. A duplicate finds the row and is skipped, which
  gives effectively-once processing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from ledgerguard.application.ports import UnitOfWork, UnitOfWorkFactory, VelocityStore
from ledgerguard.events.schemas import TOPIC_EVENTS, Envelope, TransactionEvaluatedV1


class EventPublisher(Protocol):
    async def publish(self, topic: str, envelopes: list[Envelope]) -> dict[UUID, str | None]:
        """Send and wait for acknowledgement. Returns event_id -> error (None = delivered)."""
        ...


@dataclass(frozen=True, slots=True)
class PublishResult:
    published: int
    failed: int


async def publish_outbox_batch(
    uow_factory: UnitOfWorkFactory, publisher: EventPublisher, *, batch_size: int = 100
) -> PublishResult:
    async with uow_factory() as uow:
        batch = await uow.outbox.claim_batch(batch_size)
        if not batch:
            return PublishResult(0, 0)
        results = await publisher.publish(TOPIC_EVENTS, [m.envelope for m in batch])
        delivered = [event_id for event_id, error in results.items() if error is None]
        await uow.outbox.mark_published(delivered)
        failed = {event_id: error for event_id, error in results.items() if error is not None}
        for event_id, error in failed.items():
            await uow.outbox.mark_failed(event_id, error)
        await uow.commit()
    return PublishResult(len(delivered), len(failed))


Handler = Callable[[BaseModel, Envelope, UnitOfWork], Awaitable[None]]


async def process_once(
    uow_factory: UnitOfWorkFactory, consumer_name: str, envelope: Envelope, handler: Handler
) -> bool:
    """Run ``handler`` at most once per (consumer, event). Returns False for a duplicate.

    Raises PoisonMessageError for messages that can never be processed, and lets any
    other exception propagate (the caller routes it to retry or the DLQ). On any error
    the inbox row rolls back with the side effects, so the event stays processable.
    """
    event = envelope.parse()
    async with uow_factory() as uow:
        if not await uow.inbox.record(consumer_name, envelope.event_id):
            return False
        await handler(event, envelope, uow)
        await uow.commit()
    return True


@dataclass(frozen=True, slots=True)
class BatchResult:
    processed: int
    duplicates: int


async def process_batch(
    uow_factory: UnitOfWorkFactory, consumer_name: str, envelopes: list[Envelope], handler: Handler
) -> BatchResult:
    """Process many events in ONE database transaction (consumer throughput, M3).

    One multi-row inbox insert finds the new events, the handler runs only for those,
    and one commit covers all of it. Any error rolls back the whole batch; the caller
    then falls back to ``process_once`` per message to isolate the failing one. Handlers
    must therefore be idempotent (they are anyway under at-least-once delivery).
    """
    unique: dict[UUID, Envelope] = {}
    for env in envelopes:
        unique.setdefault(env.event_id, env)  # the same event twice in one batch
    parsed = [(env, env.parse()) for env in unique.values()]  # poison raises here
    async with uow_factory() as uow:
        fresh = await uow.inbox.record_many(consumer_name, list(unique))
        for env, event in parsed:
            if env.event_id in fresh:
                await handler(event, env, uow)
        await uow.commit()
    return BatchResult(processed=len(fresh), duplicates=len(envelopes) - len(fresh))


class FeatureUpdater:
    """Keeps Redis velocity windows complete (plan s.4 cold path).

    The API also records each payment right after commit (fast path). This consumer
    makes the write reliable: if the API's write was lost, the event re-adds it. ZADD of
    the same member is idempotent, so the double write is harmless (ADR-031).
    """

    name = "feature-updater"

    def __init__(self, velocity: VelocityStore) -> None:
        self._velocity = velocity

    async def __call__(self, event: BaseModel, envelope: Envelope, uow: UnitOfWork) -> None:
        if not isinstance(event, TransactionEvaluatedV1):
            return  # not an event this consumer cares about; the inbox still records it
        await self._velocity.record(
            event.client_id, event.customer_token, str(event.transaction_id), event.created_at
        )
