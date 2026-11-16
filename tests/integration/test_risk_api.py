"""M2 against real Postgres + Redis: decisions, replay, velocity windows, fallbacks, limits."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.application.replay import replay
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.infrastructure.redis import READY_MEMBER, RedisVelocityStore
from ledgerguard.infrastructure.risk_assets import load_policy, load_rules
from ledgerguard.risk.engine import RiskEngine
from tests.pg import (
    REPO_ROOT,
    Database,
    api_client,
    migrated_database,
    new_client,
    owner_query,
    redis_server,
    register_model,
)

pytestmark = pytest.mark.integration

POLICY = "rules@1.0+model@v1+thresholds@1.0"


@pytest.fixture(scope="module")
def db() -> Iterator[Database]:
    with migrated_database() as database:
        yield database


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    with redis_server() as url:
        yield url


def payment(customer: str, amount: int = 300, merchant: str = "m_1") -> dict[str, Any]:
    return {
        "merchant_id": merchant,
        "merchant_category": "5411",
        "customer_token": customer,
        "amount_minor": amount,
        "currency": "USD",
    }


async def post(
    http: httpx.AsyncClient, key: str, idem: str, body: dict[str, Any]
) -> httpx.Response:
    return await http.post(
        "/v1/transactions", json=body, headers={"X-API-Key": key, "Idempotency-Key": idem}
    )


async def snapshots(db: Database, client_id: object) -> list[dict[str, Any]]:
    rows = await owner_query(
        db,
        "SELECT d.feature_snapshot FROM risk_decisions d JOIN transactions t "
        "ON t.id = d.transaction_id WHERE t.client_id = :c ORDER BY t.created_at",
        c=client_id,
    )
    return [r[0] for r in rows]


def test_burst_is_reviewed_with_redis_windows_and_decisions_are_stored(
    db: Database, redis_url: str
) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db, redis_url=redis_url) as http:
            responses = [
                await post(http, issued.raw_key, f"b{i}", payment("cus_burst")) for i in range(5)
            ]
            assert [r.status_code for r in responses] == [201] * 5
            bodies = [r.json() for r in responses]
            assert [b["decision"] for b in bodies] == ["approve"] * 4 + ["review"]
            assert bodies[-1]["reason_codes"] == ["VELOCITY_5M"]
            assert all(b["status"] == "evaluated" and b["policy_version"] == POLICY for b in bodies)

            decisions = await http.get(
                f"/v1/transactions/{bodies[-1]['id']}/decisions",
                headers={"X-API-Key": issued.raw_key},
            )
            assert decisions.status_code == 200
            (d,) = decisions.json()
            assert d["decision"] == "review"
            assert d["score"] == bodies[-1]["risk_score"]
            assert d["feature_snapshot"]["velocity_5m"] == 4
            assert d["evidence_refs"] == [f"{d['id']}:VELOCITY_5M"]
            assert (d["rules_version"], d["model_version"], d["thresholds_version"]) == (
                "1.0",
                "v1",
                "1.0",
            )

        # The window lives in Redis and is marked complete.
        redis = Redis.from_url(redis_url)
        try:
            key = RedisVelocityStore.key(issued.client.id, "cus_burst")
            assert await redis.zscore(key, READY_MEMBER) == 0
            assert await redis.zcard(key) == 6  # 5 payments + ready marker
            assert await redis.ttl(key) > 3600
        finally:
            await redis.aclose()

    asyncio.run(scenario())


def test_redis_down_gives_the_same_features_as_redis_up(db: Database, redis_url: str) -> None:
    """Plan A8: without Redis the hot path is slower, never different."""

    async def scenario() -> None:
        with_redis, without = await new_client(db, "up"), await new_client(db, "down")
        async with api_client(db, redis_url=redis_url) as http:
            for i in range(6):
                await post(http, with_redis.raw_key, f"k{i}", payment("cus_same", 300 + i))
        async with api_client(db) as http:  # helpers point Redis at a closed port
            for i in range(6):
                await post(http, without.raw_key, f"k{i}", payment("cus_same", 300 + i))
        assert await snapshots(db, with_redis.client.id) == await snapshots(db, without.client.id)

    asyncio.run(scenario())


def test_lost_redis_window_is_rebuilt_from_postgres(db: Database, redis_url: str) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db, redis_url=redis_url) as http:
            for i in range(3):
                await post(http, issued.raw_key, f"a{i}", payment("cus_rebuild"))
            redis = Redis.from_url(redis_url)
            await redis.delete(RedisVelocityStore.key(issued.client.id, "cus_rebuild"))
            await redis.aclose()
            await post(http, issued.raw_key, "a3", payment("cus_rebuild"))
        assert (await snapshots(db, issued.client.id))[-1]["velocity_5m"] == 3

    asyncio.run(scenario())


def test_history_is_tenant_scoped(db: Database) -> None:
    async def scenario() -> None:
        alice, bob = await new_client(db, "alice"), await new_client(db, "bob")
        async with api_client(db) as http:
            for i in range(5):
                await post(http, alice.raw_key, f"a{i}", payment("cus_shared"))
            await post(http, bob.raw_key, "b0", payment("cus_shared"))
        bob_snapshot = (await snapshots(db, bob.client.id))[0]
        assert bob_snapshot["history_count_30d"] == 0
        assert bob_snapshot["velocity_5m"] == 0

    asyncio.run(scenario())


def test_every_stored_decision_replays_exactly(db: Database) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db) as http:
            amounts = [1200, 1500, 900, 1300, 1100, 250_000, 300, 300, 300, 300, 1_200_000]
            for i, amount in enumerate(amounts):
                merchant = "m_new" if amount > 200_000 else "m_1"
                await post(http, issued.raw_key, f"r{i}", payment("cus_replay", amount, merchant))

        engine = create_async_engine(db.app_url)
        try:
            factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
            async with factory() as uow:
                model = await uow.models.get("v1")
                assert model is not None
                risk = RiskEngine(
                    rules=load_rules(REPO_ROOT, "1.0"),
                    model=model,
                    policy=load_policy(REPO_ROOT, "1.0"),
                )
                decisions = []
                for txn_id in await uow.risk_decisions.transaction_ids(None):
                    decisions += await uow.risk_decisions.list_for_transaction(txn_id)
        finally:
            await engine.dispose()
        assert len(decisions) >= len(amounts)
        results = [replay(d, risk) for d in decisions]
        assert [r.differences for r in results if not r.matches] == []
        assert {d.decision for d in decisions} >= {"approve", "decline"}

    asyncio.run(scenario())


def test_rate_limit_rejects_excess_and_fails_open(db: Database, redis_url: str) -> None:
    async def scenario() -> None:
        issued = await new_client(db)
        async with api_client(db, redis_url=redis_url, rate_limit_per_second=2) as http:
            responses = await asyncio.gather(
                *(post(http, issued.raw_key, f"rl{i}", payment(f"c{i}")) for i in range(10))
            )
        statuses = [r.status_code for r in responses]
        assert statuses.count(429) >= 4  # at most 2 per second over <= 3 seconds
        limited = next(r for r in responses if r.status_code == 429)
        assert limited.headers["retry-after"] == "1"
        assert limited.json()["type"] == "urn:ledgerguard:problem:rate-limited"

        # Redis unreachable: the limiter must not block traffic.
        async with api_client(db, rate_limit_per_second=1) as http:
            responses = await asyncio.gather(
                *(post(http, issued.raw_key, f"open{i}", payment(f"o{i}")) for i in range(5))
            )
        assert [r.status_code for r in responses] == [201] * 5

    asyncio.run(scenario())


def test_no_active_model_is_503_until_one_is_registered() -> None:
    with migrated_database(with_model=False) as empty:

        async def scenario() -> None:
            issued = await new_client(empty)
            async with api_client(empty) as http:
                resp = await post(http, issued.raw_key, "k", payment("cus_x"))
                assert resp.status_code == 503
                assert resp.json()["type"] == "urn:ledgerguard:problem:risk-engine-unavailable"
                ready = await http.get("/readyz")
                assert ready.status_code == 503
                # Nothing was written: the request failed before the transaction began.
                rows = await owner_query(empty, "SELECT count(*) FROM idempotency_records")
                assert rows[0][0] == 0

                await register_model(empty)  # no restart needed
                resp = await post(http, issued.raw_key, "k", payment("cus_x"))
                assert resp.status_code == 201
                assert (await http.get("/readyz")).status_code == 200

        asyncio.run(scenario())
