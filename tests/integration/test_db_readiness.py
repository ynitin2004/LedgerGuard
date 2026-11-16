"""Real Postgres (Testcontainers): migrations and the active model gate readiness.

Uses the same pgvector/PG16 image as docker-compose so local, CI and compose agree.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from apps.api.main import create_app
from ledgerguard.infrastructure.db.migrations import expected_head
from tests.helpers import make_settings
from tests.pg import ALEMBIC_INI, POSTGRES_IMAGE, REPO_ROOT, Database, migrate, register_model

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url().replace("@localhost:", "@127.0.0.1:")


def _readyz(database_url: str) -> tuple[int, dict[str, object]]:
    # Redis/Redpanda deliberately point at closed ports: they are optional.
    settings = make_settings(
        database_url=database_url, alembic_ini_path=ALEMBIC_INI, risk_assets_dir=REPO_ROOT
    )
    with TestClient(create_app(settings)) as client:
        resp = client.get("/readyz")
    return resp.status_code, resp.json()


def _check(body: dict[str, object], name: str) -> dict[str, object]:
    checks = body["checks"]
    assert isinstance(checks, list)
    result: dict[str, object] = next(c for c in checks if c["name"] == name)
    return result


def test_migrations_and_model_gate_readiness(database_url: str) -> None:
    db = Database(owner_url=database_url, app_url=database_url)

    # 1. Fresh database: reachable but unmigrated -> not ready.
    status, body = _readyz(database_url)
    assert status == 503
    assert "migrations not applied" in str(_check(body, "postgres")["detail"])

    # 2. Migrated but no active model -> still not ready: the API cannot decide.
    migrate(database_url)
    status, body = _readyz(database_url)
    assert status == 503
    assert _check(body, "postgres")["detail"] == f"revision={expected_head(ALEMBIC_INI)}"
    assert "no active model" in str(_check(body, "risk_engine")["detail"])

    # 3. Model registered -> ready (degraded only because Redis/Redpanda are absent).
    asyncio.run(register_model(db))
    status, body = _readyz(database_url)
    assert status == 200, body
    assert body["status"] == "degraded"
    assert _check(body, "risk_engine")["detail"] == "rules@1.0+model@v1+thresholds@1.0"

    # 4. Full downgrade/upgrade round trip works and readiness follows the schema.
    migrate(database_url, "base", down=True)
    status, _ = _readyz(database_url)
    assert status == 503
    migrate(database_url)
    asyncio.run(register_model(db))
    status, _ = _readyz(database_url)
    assert status == 200
