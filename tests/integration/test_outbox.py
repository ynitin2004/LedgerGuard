"""Outbox + inbox on real Postgres, with an in-memory broker (no Kafka needed).

Covers the delivery guarantees independently of the broker:
- the event commits atomically with the payment;
- parallel publishers (SKIP LOCKED) never send a row twice when nothing crashes;
- a publisher that crashes after sending but before commit causes re-sends, and the
  consumer inbox turns those duplicates into exactly-once side effects.
The same scenario against real Redpanda: tests/integration/test_kafka_pipeline.py.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.application.events import process_once, publish_outbox_batch
from ledgerguard.application.ports import UnitOfWork
from ledgerguard.events.schemas import Envelope, TransactionEvaluatedV1
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from tests.pg import Database, api_client, migrated_database, new_client, owner_query

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def db() -> Iterator[Database]:
    with migrated_database() as database:
        yield database


class MemoryBroker:
    """Records every message 'sent'. ``crash_after`` simulates a process crash right after
    the broker acknowledged a batch, before the publisher could commit."""

    def __init__(
        self, crash_after_batches: int | None = None, fail_ids: set[UUID] | None = None
    ) -> None:
        self.sent: list[Envelope] = []
        self.batches = 0
        self._crash_after = crash_after_batches
        self._fail = fail_ids or set()

    async def publish(self, topic: str, envelopes: list[Envelope]) -> dict[UUID, str | None]:
        self.batches += 1
        results: dict[UUID, str | None] = {}
        for env in envelopes:
            if env.event_id in self._fail:
                results[env.event_id] = "broker rejected"
            else:
                self.sent.append(env)
                results[env.event_id] = None
        if self._crash_after is not None and self.batches == self._crash_after:
            raise SystemExit("publisher killed after send, before commit")
        return results


async def create_payments(db: Database, n: int, customer_prefix: str = "cus") -> None:
    issued = await new_client(db)
    async with api_client(db) as http:
        for i in range(n):
            resp = await http.post(
                "/v1/transactions",
                json={
                    "merchant_id": "m_1",
                    "merchant_category": "5411",
                    "customer_token": f"{customer_prefix}_{i % 7}",
                    "amount_minor": 1000 + i,
                    "currency": "USD",
                },
                headers={"X-API-Key": issued.raw_key, "Idempotency-Key": f"k{i}"},
            )
            assert resp.status_code == 201


async def drain_unpublished(db: Database) -> None:
    await owner_query(
        db, "UPDATE outbox_events SET published_at = now() WHERE published_at IS NULL"
    )


async def publish_until_exhausted(
    factory: SqlAlchemyUnitOfWorkFactory, broker: MemoryBroker
) -> None:
    while (await publish_outbox_batch(factory, broker, batch_size=10)).published:
        pass


def factory_for(db: Database) -> tuple[Any, SqlAlchemyUnitOfWorkFactory]:
    engine = create_async_engine(db.app_url)  # publishers and consumers run as the app role
    return engine, SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))


def test_event_commits_with_the_payment_and_replays_add_none(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        body = {
            "merchant_id": "m_1",
            "merchant_category": "5411",
            "customer_token": "cus_atomic",
            "amount_minor": 777,
            "currency": "USD",
        }
        headers = {"X-API-Key": issued.raw_key, "Idempotency-Key": "once"}
        async with api_client(db) as http:
            first = await http.post("/v1/transactions", json=body, headers=headers)
            again = await http.post("/v1/transactions", json=body, headers=headers)
        assert again.headers["idempotent-replayed"] == "true"
        rows = await owner_query(
            db,
            "SELECT event_type, schema_version, aggregate_id, payload FROM outbox_events "
            "WHERE aggregate_id = :t",
            t=first.json()["id"],
        )
        assert len(rows) == 1  # one event per payment, none for the replay
        event_type, version, aggregate_id, payload = rows[0]
        assert (event_type, version) == ("transaction.evaluated", 1)
        event = TransactionEvaluatedV1.model_validate(payload)
        assert str(event.transaction_id) == aggregate_id
        assert event.decision == first.json()["decision"]
        assert event.risk_score == first.json()["risk_score"]

    asyncio.run(scenario())


def test_parallel_publishers_send_each_event_exactly_once_without_crashes(db: Database) -> None:
    async def scenario() -> None:
        await drain_unpublished(db)
        await create_payments(db, 60, "par")
        broker = MemoryBroker()
        engine, factory = factory_for(db)
        try:

            async def publisher() -> None:
                while (await publish_outbox_batch(factory, broker, batch_size=7)).published:
                    pass

            await asyncio.gather(*(publisher() for _ in range(4)))
        finally:
            await engine.dispose()
        counts = Counter(e.event_id for e in broker.sent)
        assert len(counts) == 60
        assert max(counts.values()) == 1  # SKIP LOCKED: disjoint batches
        left = await owner_query(
            db, "SELECT count(*) FROM outbox_events WHERE published_at IS NULL"
        )
        assert left[0][0] == 0

    asyncio.run(scenario())


def test_publisher_crash_causes_duplicates_and_the_inbox_absorbs_them(db: Database) -> None:
    async def scenario() -> None:
        await drain_unpublished(db)
        await create_payments(db, 50, "crash")
        broker = MemoryBroker(crash_after_batches=2)
        engine, factory = factory_for(db)
        effects: Counter[UUID] = Counter()

        async def handler(event: BaseModel, envelope: Envelope, uow: UnitOfWork) -> None:
            assert isinstance(event, TransactionEvaluatedV1)
            effects[event.transaction_id] += 1

        try:
            with pytest.raises(SystemExit):
                await publish_until_exhausted(factory, broker)
            # Restart: the crashed batch was never marked, so it is sent again.
            while (await publish_outbox_batch(factory, broker, batch_size=10)).published:
                pass
            sent = Counter(e.event_id for e in broker.sent)
            assert len(sent) == 50
            assert sum(sent.values()) == 60  # 10 duplicates from the crashed batch
            # Deliver everything (duplicates included, in any order) to a consumer.
            outcomes = [
                await process_once(factory, "test-consumer", env, handler)
                for env in reversed(broker.sent)
            ]
        finally:
            await engine.dispose()
        assert outcomes.count(True) == 50
        assert outcomes.count(False) == 10
        assert len(effects) == 50
        assert set(effects.values()) == {1}  # every side effect exactly once
        inbox = await owner_query(
            db, "SELECT count(*) FROM consumer_inbox WHERE consumer_name = 'test-consumer'"
        )
        assert inbox[0][0] == 50

    asyncio.run(scenario())


def test_failed_deliveries_stay_unpublished_with_the_error(db: Database) -> None:
    async def scenario() -> None:
        await drain_unpublished(db)
        await create_payments(db, 3, "fail")
        rows = await owner_query(
            db, "SELECT event_id FROM outbox_events WHERE published_at IS NULL"
        )
        bad = rows[0][0]
        engine, factory = factory_for(db)
        try:
            result = await publish_outbox_batch(factory, MemoryBroker(fail_ids={bad}))
        finally:
            await engine.dispose()
        assert (result.published, result.failed) == (2, 1)
        row = await owner_query(
            db,
            "SELECT published_at, attempt_count, last_error FROM outbox_events WHERE event_id = :e",
            e=bad,
        )
        assert row[0] == (None, 1, "broker rejected")

    asyncio.run(scenario())


def test_handler_failure_rolls_back_the_inbox_row(db: Database) -> None:
    async def scenario() -> None:
        await drain_unpublished(db)
        await create_payments(db, 1, "rollback")
        rows = await owner_query(
            db,
            "SELECT event_id, event_type, schema_version, aggregate_id, payload "
            "FROM outbox_events WHERE published_at IS NULL",
        )
        event_id, event_type, version, aggregate_id, body = rows[0]
        env = Envelope(event_id, event_type, version, aggregate_id, body)
        engine, factory = factory_for(db)
        calls = 0

        async def flaky(event: BaseModel, envelope: Envelope, uow: UnitOfWork) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("redis down")

        try:
            with pytest.raises(ConnectionError):
                await process_once(factory, "flaky", env, flaky)
            assert await process_once(factory, "flaky", env, flaky) is True  # retried
            assert await process_once(factory, "flaky", env, flaky) is False  # now a duplicate
        finally:
            await engine.dispose()
        assert calls == 2

    asyncio.run(scenario())


def test_batch_processing_dedupes_within_and_across_batches_and_rolls_back(db: Database) -> None:
    from ledgerguard.application.events import process_batch

    async def scenario() -> None:
        await drain_unpublished(db)
        await create_payments(db, 6, "batch")
        rows = await owner_query(
            db,
            "SELECT event_id, event_type, schema_version, aggregate_id, payload "
            "FROM outbox_events WHERE published_at IS NULL ORDER BY created_at",
        )
        envs = [Envelope(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        engine, factory = factory_for(db)
        effects: Counter[UUID] = Counter()
        fail = True

        async def handler(event: BaseModel, envelope: Envelope, uow: UnitOfWork) -> None:
            effects[envelope.event_id] += 1

        async def broken(event: BaseModel, envelope: Envelope, uow: UnitOfWork) -> None:
            if fail:
                raise ConnectionError("redis down")

        try:
            first = await process_batch(factory, "batcher", [*envs[:4], envs[0]], handler)
            second = await process_batch(factory, "batcher", envs, handler)
            with pytest.raises(ConnectionError):
                await process_batch(factory, "broken", envs, broken)
            fail = False
            healed = await process_batch(factory, "broken", envs, broken)
        finally:
            await engine.dispose()
        assert (first.processed, first.duplicates) == (4, 1)  # same event twice in a batch
        assert (second.processed, second.duplicates) == (2, 4)  # earlier batch's events
        assert set(effects.values()) == {1}
        assert len(effects) == 6
        assert healed.processed == 6  # the failed batch left no inbox rows behind

    asyncio.run(scenario())
