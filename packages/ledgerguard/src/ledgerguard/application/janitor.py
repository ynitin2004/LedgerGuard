"""Housekeeping use cases run by the ``janitor`` worker role."""

from __future__ import annotations

from ledgerguard.application.ports import UnitOfWorkFactory


async def purge_expired_idempotency_records(
    uow_factory: UnitOfWorkFactory, *, batch_size: int = 1000
) -> int:
    """Delete expired idempotency records in short batches; return how many were deleted.

    Each batch is its own transaction so locks stay short and the hot path is never
    blocked for long. Correctness does not depend on this job: an expired record is
    already treated as new by the claim (plan A3); purging only bounds table size.
    """
    total = 0
    while True:
        async with uow_factory() as uow:
            deleted = await uow.idempotency.delete_expired(limit=batch_size)
            await uow.commit()
        total += deleted
        if deleted < batch_size:
            return total
