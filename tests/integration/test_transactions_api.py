"""POST/GET /v1/transactions against real Postgres, connected as the least-privilege role."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.application.janitor import purge_expired_idempotency_records
from ledgerguard.domain.api_keys import hash_api_key
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from tests.pg import Database, api_client, migrated_database, new_client, owner_query

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def db() -> Iterator[Database]:
    with migrated_database() as database:
        yield database


def body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "merchant_id": "m_42",
        "merchant_category": "5411",
        "customer_token": "cus_abc",
        "amount_minor": 1250,
        "currency": "usd",
    }
    payload.update(overrides)
    return payload


def headers(key: str, idem: str | None = None, **extra: str) -> dict[str, str]:
    result = {"X-API-Key": key, **extra}
    if idem is not None:
        result["Idempotency-Key"] = idem
    return result


async def count_transactions(db: Database, client_id: object) -> int:
    rows = await owner_query(
        db, "SELECT count(*) FROM transactions WHERE client_id = :c", c=client_id
    )
    return int(rows[0][0])


def test_create_then_get(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db) as http:
            resp = await http.post(
                "/v1/transactions",
                json=body(),
                headers=headers(issued.raw_key, "k-1", **{"X-Correlation-ID": "corr-1"}),
            )
            assert resp.status_code == 201, resp.text
            assert "idempotent-replayed" not in resp.headers
            created = resp.json()
            assert created["status"] == "created"
            assert created["currency"] == "USD"  # normalised
            assert created["amount_minor"] == 1250
            assert created["decision"] is None  # risk evaluation arrives in M2

            fetched = await http.get(
                f"/v1/transactions/{created['id']}", headers=headers(issued.raw_key)
            )
            assert fetched.status_code == 200
            assert fetched.json() == created

        # Same DB transaction wrote the row, the audit event and the stored response.
        txn = await owner_query(
            db, "SELECT correlation_id FROM transactions WHERE id = :i", i=created["id"]
        )
        assert txn[0][0] == "corr-1"
        audit = await owner_query(
            db,
            "SELECT actor, action, correlation_id, metadata FROM audit_events "
            "WHERE resource_type = 'transaction' AND resource_id = :i",
            i=created["id"],
        )
        assert len(audit) == 1
        actor, action, correlation_id, metadata = audit[0]
        assert actor == f"api_client:{issued.client.id}"
        assert action == "transaction.created"
        assert correlation_id == "corr-1"
        assert metadata == {"idempotency_key": "k-1"}
        record = await owner_query(
            db,
            "SELECT response_status, response_body, expires_at - created_at "
            "FROM idempotency_records WHERE client_id = :c AND key = 'k-1'",
            c=issued.client.id,
        )
        assert record[0][0] == 201
        assert record[0][1] == created
        assert record[0][2].total_seconds() == 24 * 3600

    asyncio.run(scenario())


def test_retry_with_same_key_and_body_replays_original_response(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db) as http:
            first = await http.post(
                "/v1/transactions", json=body(), headers=headers(issued.raw_key, "k")
            )
            # Field order and currency case do not change the canonical request.
            reordered = dict(reversed(list(body(currency="USD").items())))
            second = await http.post(
                "/v1/transactions", json=reordered, headers=headers(issued.raw_key, "k")
            )
        assert first.status_code == second.status_code == 201
        assert second.headers["idempotent-replayed"] == "true"
        assert second.json() == first.json()
        assert await count_transactions(db, issued.client.id) == 1

    asyncio.run(scenario())


def test_same_key_different_body_is_409_problem(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db) as http:
            await http.post("/v1/transactions", json=body(), headers=headers(issued.raw_key, "k"))
            resp = await http.post(
                "/v1/transactions",
                json=body(amount_minor=999),
                headers=headers(issued.raw_key, "k"),
            )
        assert resp.status_code == 409
        assert resp.headers["content-type"] == "application/problem+json"
        problem = resp.json()
        assert problem["type"] == "urn:ledgerguard:problem:idempotency-key-reused"
        assert problem["status"] == 409
        assert problem["instance"] == "/v1/transactions"
        assert problem["correlation_id"] == resp.headers["x-correlation-id"]
        assert await count_transactions(db, issued.client.id) == 1

    asyncio.run(scenario())


def test_idempotency_keys_and_reads_are_scoped_per_client(db: Database) -> None:
    async def scenario() -> None:
        alice, bob = await new_client(db, "alice"), await new_client(db, "bob")
        async with api_client(db) as http:
            a = await http.post(
                "/v1/transactions", json=body(), headers=headers(alice.raw_key, "shared")
            )
            # Same key, different body, different client: not a conflict.
            b = await http.post(
                "/v1/transactions",
                json=body(amount_minor=7),
                headers=headers(bob.raw_key, "shared"),
            )
            assert a.status_code == b.status_code == 201
            assert a.json()["id"] != b.json()["id"]

            # Bob cannot read Alice's transaction; it looks exactly like a missing one.
            cross = await http.get(
                f"/v1/transactions/{a.json()['id']}", headers=headers(bob.raw_key)
            )
            missing = await http.get(
                "/v1/transactions/00000000-0000-0000-0000-000000000000",
                headers=headers(bob.raw_key),
            )
        assert cross.status_code == missing.status_code == 404
        assert cross.json()["type"] == missing.json()["type"]

    asyncio.run(scenario())


def test_authentication_failures_are_401_problems(db: Database) -> None:
    async def scenario() -> None:
        revoked = await new_client(db, "revoked")
        await owner_query(
            db, "UPDATE api_clients SET status = 'revoked' WHERE id = :i", i=revoked.client.id
        )
        async with api_client(db) as http:
            no_key = await http.post("/v1/transactions", json=body())
            unknown = await http.post(
                "/v1/transactions", json=body(), headers=headers("lg_not-a-key", "k")
            )
            revoked_resp = await http.post(
                "/v1/transactions", json=body(), headers=headers(revoked.raw_key, "k")
            )
        for resp in (no_key, unknown, revoked_resp):
            assert resp.status_code == 401
            assert resp.headers["content-type"] == "application/problem+json"
            assert resp.headers["www-authenticate"] == "ApiKey"
            assert resp.json()["type"] == "urn:ledgerguard:problem:unauthenticated"
        # The raw key is never stored, only its hash.
        stored = await owner_query(
            db, "SELECT key_hash FROM api_clients WHERE id = :i", i=revoked.client.id
        )
        assert stored[0][0] == hash_api_key(revoked.raw_key)

    asyncio.run(scenario())


def test_missing_or_invalid_idempotency_key_is_422(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db) as http:
            missing = await http.post(
                "/v1/transactions", json=body(), headers=headers(issued.raw_key)
            )
            spaces = await http.post(
                "/v1/transactions", json=body(), headers=headers(issued.raw_key, "has space")
            )
        for resp in (missing, spaces):
            assert resp.status_code == 422
            assert resp.json()["type"] == "urn:ledgerguard:problem:validation-error"
            assert resp.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]
        assert await count_transactions(db, issued.client.id) == 0

    asyncio.run(scenario())


def test_expired_key_is_treated_as_new_and_janitor_purges(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db) as http:
            first = await http.post(
                "/v1/transactions", json=body(), headers=headers(issued.raw_key, "old")
            )
            await http.post(
                "/v1/transactions", json=body(), headers=headers(issued.raw_key, "fresh")
            )
            await owner_query(
                db,
                "UPDATE idempotency_records SET expires_at = now() - interval '1 second' "
                "WHERE client_id = :c AND key = 'old'",
                c=issued.client.id,
            )
            # After expiry the key no longer protects the old body (plan A3).
            reused = await http.post(
                "/v1/transactions",
                json=body(amount_minor=5),
                headers=headers(issued.raw_key, "old"),
            )
        assert reused.status_code == 201
        assert "idempotent-replayed" not in reused.headers
        assert reused.json()["id"] != first.json()["id"]
        assert await count_transactions(db, issued.client.id) == 3

        await owner_query(
            db,
            "UPDATE idempotency_records SET expires_at = now() - interval '1 second' "
            "WHERE client_id = :c AND key = 'old'",
            c=issued.client.id,
        )
        # The janitor runs as the app role, which may DELETE idempotency records.
        engine = create_async_engine(db.app_url)
        try:
            factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
            deleted = await purge_expired_idempotency_records(factory, batch_size=1)
        finally:
            await engine.dispose()
        assert deleted >= 1
        keys = await owner_query(
            db, "SELECT key FROM idempotency_records WHERE client_id = :c", c=issued.client.id
        )
        assert [k for (k,) in keys] == ["fresh"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET actor = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
        "INSERT INTO api_clients (tenant_id, name, key_hash) "
        "VALUES (gen_random_uuid(), 'x', repeat('a', 64))",
        "CREATE TABLE sneaky (id int)",
    ],
)
def test_app_role_cannot_tamper(db: Database, statement: str) -> None:
    app_db = Database(owner_url=db.app_url, app_url=db.app_url)  # run as the app role
    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(owner_query(app_db, statement))
