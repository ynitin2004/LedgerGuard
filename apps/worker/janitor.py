"""Janitor role: periodically purge expired idempotency records (plan A3)."""

from __future__ import annotations

import asyncio

from ledgerguard.application.janitor import purge_expired_idempotency_records
from ledgerguard.config import Settings
from ledgerguard.infrastructure.db.engine import create_engine, create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.observability.logging import get_logger

log = get_logger(__name__)


async def run_janitor(
    settings: Settings, *, once: bool, interval_s: float, batch_size: int
) -> None:
    engine = create_engine(settings)
    uow_factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
    try:
        while True:
            try:
                deleted = await purge_expired_idempotency_records(
                    uow_factory, batch_size=batch_size
                )
                log.info("janitor.idempotency_purged", deleted=deleted)
            except Exception:
                if once:
                    raise
                # A transient DB error must not kill the loop; the next pass retries.
                log.exception("janitor.pass_failed")
            if once:
                return
            await asyncio.sleep(interval_s)
    finally:
        await engine.dispose()
