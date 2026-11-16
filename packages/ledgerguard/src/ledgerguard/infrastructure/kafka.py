"""Kafka (Redpanda) adapters: idempotent producer, manual-commit consumer, topics, lag.

confluent-kafka is a C extension that Windows Application Control blocks on the
primary dev PC, so workers run in Linux containers there (ADR-022). Only workers and
Kafka tests import this module; the API does not.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from confluent_kafka import Consumer, KafkaError, Message, Producer, TopicPartition
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from ledgerguard.events.schemas import TOPIC_DLQ, TOPIC_EVENTS, TOPIC_RETRY, Envelope

# Parallelism of each consumer group is capped by partitions (ADR-029 revisits).
TOPIC_PARTITIONS = {TOPIC_EVENTS: 6, TOPIC_RETRY: 6, TOPIC_DLQ: 1}


def ensure_topics(bootstrap: str, partitions: dict[str, int] | None = None) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    wanted = partitions or TOPIC_PARTITIONS
    existing = set(admin.list_topics(timeout=10).topics)
    new = [
        NewTopic(t, num_partitions=n, replication_factor=1)
        for t, n in wanted.items()
        if t not in existing
    ]
    for topic, future in admin.create_topics(new).items() if new else []:
        try:
            future.result(timeout=10)
        except Exception as exc:  # created concurrently by another worker is fine
            if "TOPIC_ALREADY_EXISTS" not in str(exc):
                raise RuntimeError(f"cannot create topic {topic}: {exc}") from exc


class KafkaPublisher:
    """Idempotent producer (enable.idempotence, acks=all): no broker-side duplicates
    from producer retries; application-level duplicates are handled by consumer inboxes."""

    def __init__(self, bootstrap: str, client_id: str) -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "client.id": client_id,
                "enable.idempotence": True,
                "acks": "all",
                "linger.ms": 5,
                "delivery.timeout.ms": 30_000,
            }
        )

    async def publish(self, topic: str, envelopes: list[Envelope]) -> dict[UUID, str | None]:
        return await asyncio.to_thread(self._publish_sync, topic, envelopes)

    def _publish_sync(self, topic: str, envelopes: list[Envelope]) -> dict[UUID, str | None]:
        results: dict[UUID, str | None] = {}
        for env in envelopes:
            self._producer.produce(
                topic,
                key=env.aggregate_id.encode(),
                value=env.value(),
                headers=list(env.headers().items()),
                on_delivery=_callback(results, env.event_id),
            )
            self._producer.poll(0)
        self._producer.flush(35)
        for env in envelopes:
            results.setdefault(env.event_id, "delivery not confirmed before timeout")
        return results

    async def send_raw(
        self, topic: str, key: bytes | None, value: bytes | None, headers: dict[str, str]
    ) -> None:
        """Forward one message (retry/DLQ) and wait for the acknowledgement."""
        await asyncio.to_thread(self._send_raw_sync, topic, key, value, headers)

    def _send_raw_sync(
        self, topic: str, key: bytes | None, value: bytes | None, headers: dict[str, str]
    ) -> None:
        errors: list[str] = []

        def on_delivery(err: KafkaError | None, _msg: Message) -> None:
            if err is not None:
                errors.append(str(err))

        self._producer.produce(
            topic, key=key, value=value, headers=list(headers.items()), on_delivery=on_delivery
        )
        if self._producer.flush(35) > 0 or errors:
            raise RuntimeError(f"could not deliver to {topic}: {errors or 'timeout'}")


def _callback(results: dict[UUID, str | None], event_id: UUID):  # type: ignore[no-untyped-def]
    def on_delivery(err: KafkaError | None, _msg: Message) -> None:
        results[event_id] = None if err is None else str(err)

    return on_delivery


@dataclass(frozen=True, slots=True)
class Received:
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    headers: dict[str, str]
    raw: Message


class KafkaConsumer:
    """Manual offset commits (enable.auto.commit=false): an offset is committed only
    after the message's database transaction committed (or it was routed to retry/DLQ)."""

    def __init__(self, bootstrap: str, group_id: str, topics: Iterable[str]) -> None:
        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap,
                "group.id": group_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "enable.partition.eof": False,
                # Fail over a dead consumer in ~10 s instead of the 45 s default (seen as a
                # 30 s stall after a kill in the M3 chaos run).
                "session.timeout.ms": 10_000,
                "heartbeat.interval.ms": 3_000,
            }
        )
        self._consumer.subscribe(list(topics))

    async def poll(self, timeout_s: float = 1.0) -> Received | None:
        batch = await self.poll_batch(1, timeout_s)
        return batch[0] if batch else None

    async def poll_batch(self, max_messages: int, timeout_s: float = 1.0) -> list[Received]:
        """Up to ``max_messages`` in one thread hop (throughput: one call per batch)."""
        messages = await asyncio.to_thread(self._consumer.consume, max_messages, timeout_s)
        return [self._received(m) for m in messages]

    @staticmethod
    def _received(msg: Message) -> Received:
        if msg.error() is not None:
            raise RuntimeError(f"consumer error: {msg.error()}")
        raw_headers = msg.headers() or []
        pairs = raw_headers.items() if isinstance(raw_headers, dict) else raw_headers
        headers = {str(k): _text(v) for k, v in pairs}
        topic, partition, offset = msg.topic(), msg.partition(), msg.offset()
        return Received(
            topic or "",
            partition if partition is not None else -1,
            offset if offset is not None else -1,
            _bytes(msg.key()),
            _bytes(msg.value()),
            headers,
            msg,
        )

    async def commit_position(self) -> None:
        """Commit the current position of every assigned partition, i.e. everything returned
        so far. Call only after all of it was processed (at-least-once)."""

        def commit_sync() -> None:
            self._consumer.commit(asynchronous=False)

        await asyncio.to_thread(commit_sync)

    async def commit(self, received: Received) -> None:
        def commit_sync() -> None:
            self._consumer.commit(message=received.raw, asynchronous=False)

        await asyncio.to_thread(commit_sync)

    def lag(self) -> dict[tuple[str, int], int]:
        """Messages behind the end of each assigned partition (consumer lag metric)."""
        lags: dict[tuple[str, int], int] = {}
        assigned = self._consumer.assignment()
        if not assigned:
            return lags
        committed = {
            (tp.topic, tp.partition): tp.offset
            for tp in self._consumer.committed(assigned, timeout=5)
        }
        for tp in assigned:
            _low, high = self._consumer.get_watermark_offsets(tp, timeout=5, cached=False)
            done = committed.get((tp.topic, tp.partition), -1)
            lags[(tp.topic, tp.partition)] = max(0, high - (done if done >= 0 else 0))
        return lags

    def close(self) -> None:
        self._consumer.close()


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else value


def _bytes(value: str | bytes | None) -> bytes | None:
    return value.encode() if isinstance(value, str) else value


def topic_partition_count(bootstrap: str, topic: str) -> int:
    admin = AdminClient({"bootstrap.servers": bootstrap})
    return len(admin.list_topics(topic, timeout=10).topics[topic].partitions)


def end_offsets(bootstrap: str, topic: str) -> int:
    """Total number of messages ever written to ``topic`` (sum of high watermarks)."""
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": "lg-offset-probe"})
    try:
        total = 0
        for p in range(topic_partition_count(bootstrap, topic)):
            _low, high = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            total += high
        return total
    finally:
        consumer.close()
