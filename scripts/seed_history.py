"""Seed synthetic payment history for one API client (demo and load tests, plan A4).

    uv run python scripts/seed_history.py --name demo            # new client, prints its key
    uv run python scripts/seed_history.py --name demo --customers 500

Generates the synthetic data set (same generator as training), shifts it so the last
day ends now, and inserts it as that client's past transactions (status settled_sim or
failed_sim, no decision: history from "before" the system). Connects as the owner.
Customer tokens are cus_00000 .. cus_<N-1>; the load test picks from that range.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.application.api_clients import register_api_client
from ledgerguard.config import get_settings
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.models import TransactionRow
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.synthetic import SyntheticConfig, generate

BATCH = 5000


async def seed(name: str, customers: int, seed_value: int, database_url: str) -> str:
    now = datetime.now(UTC)
    txns = generate(SyntheticConfig(seed=seed_value, customers=customers, end=now))
    engine = create_async_engine(database_url)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        issued = await register_api_client(factory, name=name)
        client_id: UUID = issued.client.id
        rows: list[dict[str, Any]] = [
            {
                "client_id": client_id,
                "merchant_id": t.merchant_id,
                "merchant_category": t.merchant_category,
                "customer_token": t.customer_token,
                "amount_minor": t.amount_minor,
                "currency": t.currency,
                "status": t.status,
                "created_at": t.created_at,
            }
            for t in txns
            if t.created_at < now
        ]
        async with engine.begin() as conn:
            for i in range(0, len(rows), BATCH):
                await conn.execute(insert(TransactionRow), rows[i : i + BATCH])
    finally:
        await engine.dispose()
    return (
        f"client_id: {client_id}\napi_key:   {issued.raw_key}\n"
        f"seeded {len(rows)} transactions for {customers} customers "
        f"(cus_00000..cus_{customers - 1:05d}), ending now\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic history for a new API client.")
    parser.add_argument("--name", default="demo")
    parser.add_argument("--customers", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=11, help="differs from the training seed")
    args = parser.parse_args()
    sys.stdout.write(
        asyncio.run(seed(args.name, args.customers, args.seed, get_settings().owner_database_url))
    )


if __name__ == "__main__":
    main()
