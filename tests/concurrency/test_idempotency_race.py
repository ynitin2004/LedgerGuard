"""M1 acceptance: concurrent duplicates are resolved by the Postgres primary key (plan A2).

The requests really overlap: they run concurrently against a 20-connection pool, so
most duplicates reach the INSERT while the winner's transaction is still open and
must wait on the key instead of reading a committed row.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterator

import httpx
import pytest

from tests.pg import Database, api_client, migrated_database, new_client, owner_query

pytestmark = pytest.mark.integration

N = 50
BODY = {
    "merchant_id": "m_race",
    "merchant_category": "5812",
    "customer_token": "cus_race",
    "amount_minor": 4200,
    "currency": "EUR",
}


@pytest.fixture(scope="module")
def db() -> Iterator[Database]:
    with migrated_database() as database:
        yield database


async def _burst(
    http: httpx.AsyncClient, key: str, bodies: list[dict[str, object]]
) -> list[httpx.Response]:
    return await asyncio.gather(
        *(
            http.post(
                "/v1/transactions",
                json=b,
                headers={"X-API-Key": key, "Idempotency-Key": "same-key"},
            )
            for b in bodies
        )
    )


def test_50_concurrent_identical_requests_create_exactly_one_transaction(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db, "racer")
        async with api_client(db, db_pool_size=10, db_max_overflow=10) as http:
            responses = await _burst(http, issued.raw_key, [BODY] * N)

        assert [r.status_code for r in responses] == [201] * N
        bodies = [r.json() for r in responses]
        assert all(b == bodies[0] for b in bodies)  # 50 identical responses
        replayed = Counter(r.headers.get("idempotent-replayed") for r in responses)
        assert replayed == {None: 1, "true": N - 1}

        rows = await owner_query(
            db, "SELECT id FROM transactions WHERE client_id = :c", c=issued.client.id
        )
        assert [str(r[0]) for r in rows] == [bodies[0]["id"]]
        audit = await owner_query(
            db, "SELECT count(*) FROM audit_events WHERE resource_id = :i", i=bodies[0]["id"]
        )
        assert audit[0][0] == 1

    asyncio.run(scenario())


def test_concurrent_different_bodies_on_one_key_yield_one_winner_and_409s(
    db: Database,
) -> None:
    async def scenario() -> None:
        issued = await new_client(db, "conflicted")
        bodies: list[dict[str, object]] = [{**BODY, "amount_minor": i + 1} for i in range(20)]
        async with api_client(db, db_pool_size=10, db_max_overflow=10) as http:
            responses = await _burst(http, issued.raw_key, bodies)

        statuses = Counter(r.status_code for r in responses)
        assert statuses == {201: 1, 409: 19}
        rows = await owner_query(
            db, "SELECT count(*) FROM transactions WHERE client_id = :c", c=issued.client.id
        )
        assert rows[0][0] == 1

    asyncio.run(scenario())
