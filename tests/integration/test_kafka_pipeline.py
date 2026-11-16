"""M3 acceptance on real Redpanda + Postgres + Redis.

"Kill the publisher mid-batch under load: no lost events, duplicates deduped (test
asserts counts); consumer lag metric visible."

Needs confluent-kafka, which Windows Application Control blocks on the primary dev PC:
skipped there, runs in CI and in `make test-linux` / `./scripts/dev.ps1 test-linux`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

try:
    import confluent_kafka  # noqa: F401

    KAFKA_OK = True
except ImportError:
    KAFKA_OK = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not KAFKA_OK, reason="confluent-kafka cannot load here (ADR-022)"),
]

REDPANDA_IMAGE = "docker.redpanda.com/redpandadata/redpanda:v24.2.7"
N_PAYMENTS = 150


@pytest.fixture(scope="module")
def db() -> Iterator[Any]:
    from tests.pg import migrated_database

    with migrated_database() as database:
        yield database


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    from tests.pg import redis_server

    with redis_server() as url:
        yield url


@pytest.fixture
def bootstrap() -> Iterator[str]:
    from testcontainers.community.kafka import RedpandaContainer

    from ledgerguard.infrastructure.kafka import ensure_topics

    with RedpandaContainer(REDPANDA_IMAGE) as redpanda:
        server = redpanda.get_bootstrap_server()
        ensure_topics(server)
        yield server


async def _wait_for(predicate: Any, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def test_publisher_killed_mid_batch_under_load_loses_nothing(
    db: Any, redis_url: str, bootstrap: str
) -> None:
    from apps.worker.events import ConsumerRunner, _lag
    from ledgerguard.application.events import FeatureUpdater, publish_outbox_batch
    from ledgerguard.events.schemas import TOPIC_EVENTS, Envelope
    from ledgerguard.infrastructure.db.engine import create_session_factory
    from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
    from ledgerguard.infrastructure.kafka import KafkaPublisher, end_offsets
    from ledgerguard.infrastructure.redis import RedisVelocityStore, create_redis
    from tests.pg import api_client, new_client, owner_query

    class KilledMidBatch(KafkaPublisher):
        """Sends the first half of its 3rd batch to the broker, then the process 'dies'."""

        batches = 0

        async def publish(self, topic: str, envelopes: list[Envelope]) -> dict[UUID, str | None]:
            KilledMidBatch.batches += 1
            if KilledMidBatch.batches == 3:
                await super().publish(topic, envelopes[: len(envelopes) // 2])
                raise SystemExit("publisher killed mid-batch")
            return await super().publish(topic, envelopes)

    async def scenario() -> None:
        issued = await new_client(db)
        engine = create_async_engine(db.app_url)
        redis = create_redis(redis_url, 1.0)
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        stop = asyncio.Event()
        updater = FeatureUpdater(RedisVelocityStore(redis))
        consumer = ConsumerRunner(
            updater.name, updater, factory, bootstrap, KafkaPublisher(bootstrap, "t")
        )
        consumer_task = asyncio.create_task(consumer.run(stop))

        async def load() -> None:  # payments keep arriving while the publisher runs
            async with api_client(db) as http:
                for start in range(0, N_PAYMENTS, 10):
                    await asyncio.gather(
                        *(
                            http.post(
                                "/v1/transactions",
                                json={
                                    "merchant_id": "m_1",
                                    "merchant_category": "5411",
                                    "customer_token": f"cus_{i % 13}",
                                    "amount_minor": 1000 + i,
                                    "currency": "USD",
                                },
                                headers={"X-API-Key": issued.raw_key, "Idempotency-Key": f"L{i}"},
                            )
                            for i in range(start, start + 10)
                        )
                    )

        async def publisher(p: KafkaPublisher) -> None:
            while True:
                result = await publish_outbox_batch(factory, p, batch_size=20)
                if result.published == 0:
                    await asyncio.sleep(0.05)
                    pending = await owner_query(
                        db, "SELECT count(*) FROM outbox_events WHERE published_at IS NULL"
                    )
                    if load_task.done() and pending[0][0] == 0:
                        return

        try:
            load_task = asyncio.create_task(load())
            with pytest.raises(SystemExit):
                await publisher(KilledMidBatch(bootstrap, "doomed"))
            await publisher(KafkaPublisher(bootstrap, "restarted"))  # the restart
            await load_task

            async def all_consumed() -> bool:
                rows = await owner_query(
                    db,
                    "SELECT count(*) FROM consumer_inbox WHERE consumer_name = 'feature-updater'",
                )
                return bool(rows[0][0] == N_PAYMENTS)

            await _wait_for(all_consumed, 90, "every event to be consumed")
            await asyncio.sleep(2)  # let any in-flight duplicate arrive and be skipped
        finally:
            stop.set()
            await consumer_task
            await redis.aclose()
            await engine.dispose()

        sent = await asyncio.to_thread(end_offsets, bootstrap, TOPIC_EVENTS)
        assert sent > N_PAYMENTS, "the kill should have produced duplicate sends"
        inbox = await owner_query(
            db, "SELECT count(*) FROM consumer_inbox WHERE consumer_name = 'feature-updater'"
        )
        assert inbox[0][0] == N_PAYMENTS  # no loss, no double processing
        assert consumer.processed == N_PAYMENTS
        assert consumer.duplicates == sent - N_PAYMENTS
        txns = await owner_query(
            db,
            "SELECT id, customer_token FROM transactions WHERE client_id = :c",
            c=issued.client.id,
        )
        check = Redis.from_url(redis_url)
        try:
            for txn_id, customer in txns:
                key = RedisVelocityStore.key(issued.client.id, customer)
                assert await check.zscore(key, str(txn_id)) is not None
        finally:
            await check.aclose()
        lag = {k: v for k, v in _lag.items() if k[0] == "feature-updater" and k[1] == TOPIC_EVENTS}
        assert lag  # the metric exists per partition
        assert all(v == 0 for v in lag.values()), lag

    asyncio.run(scenario())


def test_poison_and_exhausted_retries_reach_the_dlq_and_replay_recovers(
    db: Any, bootstrap: str
) -> None:
    from apps.worker.events import ConsumerRunner, replay_dlq
    from ledgerguard.application.ports import UnitOfWork
    from ledgerguard.events.schemas import TOPIC_DLQ, TOPIC_EVENTS, Envelope
    from ledgerguard.infrastructure.db.engine import create_session_factory
    from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
    from ledgerguard.infrastructure.kafka import KafkaPublisher, end_offsets
    from tests.pg import owner_query
    from tests.unit.test_events import envelope

    healthy = False

    async def flaky(event: BaseModel, env: Envelope, uow: UnitOfWork) -> None:
        if not healthy:
            raise ConnectionError("dependency down")

    async def scenario() -> None:
        nonlocal healthy
        engine = create_async_engine(db.app_url)
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        forwarder = KafkaPublisher(bootstrap, "test")
        stop = asyncio.Event()
        runner = ConsumerRunner(
            "dlq-test", flaky, factory, bootstrap, forwarder, max_attempts=3, base_delay_s=0.2
        )
        task = asyncio.create_task(runner.run(stop))
        good = envelope()
        try:
            await forwarder.send_raw(TOPIC_EVENTS, b"k", b"{}", {"event_type": "?"})  # poison
            await forwarder.publish(TOPIC_EVENTS, [good])  # valid, but the handler fails

            async def two_dead() -> bool:
                return await asyncio.to_thread(end_offsets, bootstrap, TOPIC_DLQ) == 2

            await _wait_for(two_dead, 60, "poison + exhausted message in the DLQ")
            healthy = True
            replayed = await replay_dlq(bootstrap, forwarder, event_id=str(good.event_id))
            assert replayed == 1

            async def recovered() -> bool:
                rows = await owner_query(
                    db,
                    "SELECT count(*) FROM consumer_inbox WHERE consumer_name = 'dlq-test' "
                    "AND event_id = :e",
                    e=good.event_id,
                )
                return bool(rows[0][0] == 1)

            await _wait_for(recovered, 60, "the replayed event to be processed")
        finally:
            stop.set()
            await task
            await engine.dispose()

    asyncio.run(scenario())
