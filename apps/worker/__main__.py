"""Worker entrypoint: ``python -m apps.worker <role> [options]``.

Roles: ``janitor`` (M1); ``outbox-publisher``, ``feature-updater``, ``replay-dlq``,
``reconcile`` (M3).
Planned: investigator (M4), webhook-sender (M5), rag-ingest (M7).

Kafka roles need confluent-kafka, which only loads on Linux on the primary dev PC:
run them with ``docker compose --profile workers up`` (ADR-022).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence

from apps.worker.janitor import run_janitor
from ledgerguard.config import Settings, get_settings
from ledgerguard.infrastructure.db.engine import create_engine, create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.observability.logging import configure_logging, get_logger
from ledgerguard.observability.tracing import configure_process_telemetry

log = get_logger(__name__)


async def _until_signalled(work: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Not supported by the Windows event loop (Ctrl+C raises there instead).
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await work(stop)


async def _publisher(settings: Settings, stop: asyncio.Event) -> None:
    from apps.worker.events import run_publisher
    from ledgerguard.infrastructure.kafka import KafkaPublisher, ensure_topics

    ensure_topics(settings.kafka_bootstrap_servers)
    engine = create_engine(settings)
    try:
        uow_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        publisher = KafkaPublisher(settings.kafka_bootstrap_servers, "outbox-publisher")
        log.info("worker.started", role="outbox-publisher")
        await run_publisher(uow_factory, publisher, stop)
    finally:
        await engine.dispose()


async def _feature_updater(settings: Settings, stop: asyncio.Event) -> None:
    from apps.worker.events import ConsumerRunner
    from ledgerguard.application.events import FeatureUpdater
    from ledgerguard.infrastructure.kafka import KafkaPublisher, ensure_topics
    from ledgerguard.infrastructure.redis import RedisVelocityStore, create_redis

    ensure_topics(settings.kafka_bootstrap_servers)
    engine = create_engine(settings)
    # Background worker: a slower Redis timeout than the hot path is fine here.
    redis = create_redis(settings.redis_url, timeout_s=1.0)
    try:
        uow_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        handler = FeatureUpdater(RedisVelocityStore(redis))
        runner = ConsumerRunner(
            handler.name,
            handler,
            uow_factory,
            settings.kafka_bootstrap_servers,
            KafkaPublisher(settings.kafka_bootstrap_servers, handler.name),
        )
        log.info("worker.started", role=handler.name)
        await runner.run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()


async def _replay(settings: Settings, event_id: str | None) -> None:
    from apps.worker.events import replay_dlq
    from ledgerguard.infrastructure.kafka import KafkaPublisher

    count = await replay_dlq(
        settings.kafka_bootstrap_servers,
        KafkaPublisher(settings.kafka_bootstrap_servers, "replay-dlq"),
        event_id=event_id,
    )
    sys.stdout.write(f"replayed {count} message(s) from the DLQ to the retry topic\n")


async def _reconcile(settings: Settings, strict: bool) -> int:
    """Counts across the pipeline: every outbox event published and consumed exactly once."""
    from sqlalchemy import text

    from ledgerguard.events.schemas import TOPIC_DLQ, TOPIC_EVENTS
    from ledgerguard.infrastructure.kafka import end_offsets

    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            counts = (
                await conn.execute(
                    text(
                        "SELECT count(*), count(*) FILTER (WHERE published_at IS NULL) "
                        "FROM outbox_events"
                    )
                )
            ).one()
            per_consumer = (
                await conn.execute(
                    text("SELECT consumer_name, count(*) FROM consumer_inbox GROUP BY 1")
                )
            ).all()
    finally:
        await engine.dispose()
    outbox, unpublished = int(counts[0]), int(counts[1])
    inbox = {str(name): int(n) for name, n in per_consumer}
    sent = end_offsets(settings.kafka_bootstrap_servers, TOPIC_EVENTS)
    dead = end_offsets(settings.kafka_bootstrap_servers, TOPIC_DLQ)
    consumed = inbox.get("feature-updater", 0)
    ok = unpublished == 0 and consumed == outbox
    sys.stdout.write(
        f"outbox events:                {outbox}\n"
        f"  still unpublished:          {unpublished}\n"
        f"messages on {TOPIC_EVENTS}:      {sent} (re-sent duplicates: {max(0, sent - outbox)})\n"
        f"processed by feature-updater: {consumed}\n"
        f"dead-lettered:                {dead}\n"
        + (
            "RECONCILED: every event published and processed exactly once\n"
            if ok
            else "NOT RECONCILED\n"
        )
    )
    return 0 if ok or not strict else 1


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m apps.worker")
    roles = parser.add_subparsers(dest="role", required=True)

    janitor = roles.add_parser("janitor", help="purge expired idempotency records")
    janitor.add_argument("--once", action="store_true", help="run one pass and exit")
    janitor.add_argument("--interval", type=float, default=300.0, help="seconds between passes")
    janitor.add_argument("--batch-size", type=int, default=1000)

    roles.add_parser("outbox-publisher", help="publish the transactional outbox to Kafka")
    roles.add_parser("feature-updater", help="consume txn.events into Redis velocity windows")
    reconcile = roles.add_parser("reconcile", help="compare outbox, topic and inbox counts")
    reconcile.add_argument("--strict", action="store_true", help="exit 1 if not reconciled")
    replay = roles.add_parser("replay-dlq", help="send dead-lettered events back for retry")
    target = replay.add_mutually_exclusive_group(required=True)
    target.add_argument("--event-id")
    target.add_argument("--all", action="store_true")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if args.role == "janitor":
        asyncio.run(
            run_janitor(
                settings, once=args.once, interval_s=args.interval, batch_size=args.batch_size
            )
        )
    elif args.role == "outbox-publisher":
        configure_process_telemetry(settings, "ledgerguard-outbox-publisher")
        asyncio.run(_until_signalled(lambda stop: _publisher(settings, stop)))
    elif args.role == "feature-updater":
        configure_process_telemetry(settings, "ledgerguard-feature-updater")
        asyncio.run(_until_signalled(lambda stop: _feature_updater(settings, stop)))
    elif args.role == "replay-dlq":
        asyncio.run(_replay(settings, None if args.all else args.event_id))
    elif args.role == "reconcile":
        sys.exit(asyncio.run(_reconcile(settings, args.strict)))


if __name__ == "__main__":
    main()
