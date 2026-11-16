"""Event worker roles: outbox-publisher, consumers (feature-updater), replay-dlq."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterable
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

from ledgerguard.application.events import (
    EventPublisher,
    Handler,
    process_batch,
    process_once,
    publish_outbox_batch,
)
from ledgerguard.application.ports import UnitOfWorkFactory
from ledgerguard.events.retry import route_failure
from ledgerguard.events.schemas import TOPIC_DLQ, TOPIC_EVENTS, TOPIC_RETRY, Envelope
from ledgerguard.infrastructure.kafka import KafkaConsumer, KafkaPublisher, Received
from ledgerguard.observability.logging import get_logger

log = get_logger(__name__)
meter = metrics.get_meter("ledgerguard.worker")

# Latest observed values, read by the OpenTelemetry gauge callbacks below.
_lag: dict[tuple[str, str, int], int] = {}
_backlog: dict[str, float] = {}


def _observe_lag(_options: CallbackOptions) -> Iterable[Observation]:
    for (group, topic, partition), value in list(_lag.items()):
        yield Observation(value, {"consumer": group, "topic": topic, "partition": partition})


def _observe_backlog(_options: CallbackOptions) -> Iterable[Observation]:
    if "count" in _backlog:
        yield Observation(_backlog["count"])


def _observe_backlog_age(_options: CallbackOptions) -> Iterable[Observation]:
    if "age" in _backlog:
        yield Observation(_backlog["age"])


meter.create_observable_gauge(
    "consumer_lag", callbacks=[_observe_lag], description="Messages behind per partition"
)
meter.create_observable_gauge(
    "outbox_backlog", callbacks=[_observe_backlog], description="Unpublished outbox rows"
)
meter.create_observable_gauge(
    "outbox_oldest_age_seconds",
    callbacks=[_observe_backlog_age],
    description="Age of the oldest unpublished outbox row",
)


async def run_publisher(
    uow_factory: UnitOfWorkFactory,
    publisher: EventPublisher,
    stop: asyncio.Event,
    *,
    batch_size: int = 100,
    idle_sleep_s: float = 0.2,
) -> None:
    last_report = 0.0
    while not stop.is_set():
        try:
            result = await publish_outbox_batch(uow_factory, publisher, batch_size=batch_size)
            if result.failed:
                log.warning("outbox.delivery_failed", failed=result.failed)
            if time.monotonic() - last_report > 5:
                async with uow_factory() as uow:
                    backlog = await uow.outbox.backlog()
                _backlog["count"] = backlog.unpublished
                _backlog["age"] = backlog.oldest_age_s or 0.0
                last_report = time.monotonic()
                log.info(
                    "outbox.backlog",
                    unpublished=backlog.unpublished,
                    oldest_age_s=backlog.oldest_age_s,
                )
            if result.published == 0 and result.failed == 0:
                await _sleep(stop, idle_sleep_s)
        except Exception:
            log.exception("outbox.publish_failed")
            await _sleep(stop, 1.0)


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


class ConsumerRunner:
    """Runs one consumer: main topic, plus its own messages on the retry topic.

    Two Kafka consumer groups (``<name>`` and ``<name>.retry``) so waiting for a retry's
    backoff never blocks fresh events. Offsets are committed only after the message was
    processed, skipped as a duplicate, or forwarded to retry/DLQ.
    """

    def __init__(
        self,
        name: str,
        handler: Handler,
        uow_factory: UnitOfWorkFactory,
        bootstrap: str,
        forwarder: KafkaPublisher,
        *,
        max_attempts: int | None = None,
        base_delay_s: float | None = None,
        batch_size: int = 200,
    ) -> None:
        self.name = name
        self._handler = handler
        self._uow_factory = uow_factory
        self._bootstrap = bootstrap
        self._forwarder = forwarder
        self._route_kwargs: dict[str, Any] = {}
        if max_attempts is not None:
            self._route_kwargs["max_attempts"] = max_attempts
        if base_delay_s is not None:
            self._route_kwargs["base_delay_s"] = base_delay_s
        self._batch_size = batch_size
        self.processed = 0
        self.duplicates = 0

    async def run(self, stop: asyncio.Event) -> None:
        await asyncio.gather(
            self._loop(
                KafkaConsumer(self._bootstrap, self.name, [TOPIC_EVENTS]), stop, retry=False
            ),
            self._loop(
                KafkaConsumer(self._bootstrap, f"{self.name}.retry", [TOPIC_RETRY]),
                stop,
                retry=True,
            ),
        )

    async def _loop(self, consumer: KafkaConsumer, stop: asyncio.Event, *, retry: bool) -> None:
        group = f"{self.name}.retry" if retry else self.name
        last_lag = 0.0
        sampled = False

        def sample_lag() -> bool:
            lags = consumer.lag()
            for (topic, partition), value in lags.items():
                _lag[(group, topic, partition)] = value
            return bool(lags)

        try:
            while not stop.is_set():
                # Every 5 s, and on every pass until partitions are first assigned.
                if not sampled or time.monotonic() - last_lag > 5:
                    sampled = sample_lag()
                    last_lag = time.monotonic()
                if retry:
                    await self._retry_step(consumer, stop)
                    continue
                # Main topic: a batch per poll, one offset commit per batch (M3 throughput
                # fix). Offsets are committed only after the whole batch was handled, so a
                # crash re-delivers part of a batch; the inbox absorbs those duplicates.
                batch = await consumer.poll_batch(self._batch_size, 1.0)
                if batch:
                    await self._handle_batch(batch)
                    await consumer.commit_position()
            sample_lag()  # final value on shutdown
        finally:
            consumer.close()

    async def _retry_step(self, consumer: KafkaConsumer, stop: asyncio.Event) -> None:
        received = await consumer.poll(1.0)
        if received is None:
            return
        if received.headers.get("target_consumer") == self.name:
            due = float(received.headers.get("retry_at", "0"))
            await _sleep(stop, max(0.0, due - time.time()))
            if stop.is_set():
                return  # not committed: redelivered after restart
            await self._handle(received)
        await consumer.commit(received)  # handled, or another consumer's retry

    async def _handle_batch(self, batch: list[Received]) -> None:
        """Fast path: the whole batch in one transaction. If anything fails (poison message,
        handler error), roll back and handle each message on its own so only the failing
        one goes to retry/DLQ."""
        try:
            envelopes = [Envelope.from_message(m.key, m.value, m.headers) for m in batch]
            result = await process_batch(self._uow_factory, self.name, envelopes, self._handler)
        except Exception as exc:
            log.warning(
                "consumer.batch_fallback",
                consumer=self.name,
                size=len(batch),
                error=type(exc).__name__,
            )
            for received in batch:
                await self._handle(received)
            return
        self.processed += result.processed
        self.duplicates += result.duplicates
        if result.duplicates:
            log.info("consumer.duplicates_skipped", consumer=self.name, count=result.duplicates)

    async def _handle(self, msg: Received) -> None:
        attempt = int(msg.headers.get("attempt", "1"))
        try:
            envelope = Envelope.from_message(msg.key, msg.value, msg.headers)
            if await process_once(self._uow_factory, self.name, envelope, self._handler):
                self.processed += 1
            else:
                self.duplicates += 1
                log.info(
                    "consumer.duplicate_skipped",
                    consumer=self.name,
                    event_id=str(envelope.event_id),
                )
        except Exception as exc:
            route = route_failure(attempt, exc, **self._route_kwargs)
            headers = {
                **{
                    k: v
                    for k, v in msg.headers.items()
                    if k not in {"attempt", "retry_at", "error"}
                },
                "target_consumer": self.name,
                "attempt": str(route.next_attempt),
                "error": route.reason,
            }
            if route.destination == "retry":
                headers["retry_at"] = f"{time.time() + route.delay_s:.3f}"
                await self._forwarder.send_raw(TOPIC_RETRY, msg.key, msg.value, headers)
                log.warning(
                    "consumer.retry_scheduled",
                    consumer=self.name,
                    attempt=route.next_attempt,
                    delay_s=round(route.delay_s, 2),
                    error=route.reason,
                )
            else:
                await self._forwarder.send_raw(TOPIC_DLQ, msg.key, msg.value, headers)
                log.error(
                    "consumer.dead_lettered",
                    consumer=self.name,
                    attempt=attempt,
                    error=route.reason,
                )


async def replay_dlq(
    bootstrap: str, forwarder: KafkaPublisher, *, event_id: str | None, idle_timeout_s: float = 5.0
) -> int:
    """Send dead-lettered messages back through the retry topic to the consumer that failed.

    Reads the whole DLQ with a throwaway consumer group (the DLQ itself is never
    consumed destructively). ``event_id=None`` replays everything.
    """
    consumer = KafkaConsumer(bootstrap, f"lg-replay-{time.time_ns()}", [TOPIC_DLQ])
    replayed = 0
    idle_since = time.monotonic()
    try:
        while time.monotonic() - idle_since < idle_timeout_s:
            msg = await consumer.poll(1.0)
            if msg is None:
                continue
            idle_since = time.monotonic()
            if event_id is not None and msg.headers.get("event_id") != event_id:
                continue
            headers = {
                **msg.headers,
                "attempt": "1",
                "retry_at": f"{time.time():.3f}",
                "replayed": "true",
            }
            await forwarder.send_raw(TOPIC_RETRY, msg.key, msg.value, headers)
            replayed += 1
    finally:
        consumer.close()
    return replayed
