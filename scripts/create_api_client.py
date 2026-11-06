"""Create an API client and print its key once (admin tool; admin API arrives in M5).

    uv run python scripts/create_api_client.py --name demo
    uv run python scripts/create_api_client.py --name ci --key-only   # for scripts

Connects as the schema owner (LG_MIGRATION_DATABASE_URL): the runtime role cannot
insert into api_clients. Only the SHA-256 hash of the key is stored, so a lost key
cannot be recovered; create a new client instead.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.application.api_clients import IssuedApiKey, register_api_client
from ledgerguard.config import get_settings
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory


async def _create(name: str, tenant_id: UUID | None) -> IssuedApiKey:
    engine = create_async_engine(get_settings().owner_database_url)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        return await register_api_client(factory, name=name, tenant_id=tenant_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True)
    parser.add_argument("--tenant-id", type=UUID, default=None, help="default: a new tenant")
    parser.add_argument("--key-only", action="store_true", help="print only the raw key")
    args = parser.parse_args()

    issued = asyncio.run(_create(args.name, args.tenant_id))
    if args.key_only:
        sys.stdout.write(issued.raw_key + "\n")
        return
    sys.stdout.write(
        f"client_id: {issued.client.id}\n"
        f"tenant_id: {issued.client.tenant_id}\n"
        f"api_key:   {issued.raw_key}\n"
        "Store the key now: it is not shown again.\n"
    )


if __name__ == "__main__":
    main()
