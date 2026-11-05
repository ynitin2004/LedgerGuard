"""Real Postgres (Testcontainers): migrations + readiness against the schema head.

Uses the same pgvector/PG16 image as docker-compose so local, CI and compose agree.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from testcontainers.community.postgres import PostgresContainer

from apps.api.main import create_app
from ledgerguard.infrastructure.db.migrations import alembic_config, expected_head
from tests.helpers import make_settings

pytestmark = pytest.mark.integration

POSTGRES_IMAGE = "pgvector/pgvector:0.8.0-pg16"
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "migrations" / "alembic.ini"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


def _readyz(database_url: str) -> tuple[int, dict[str, object]]:
    # Redis/Redpanda deliberately point at closed ports: they are optional.
    app = create_app(make_settings(database_url=database_url, alembic_ini_path=ALEMBIC_INI))
    with TestClient(app) as client:
        resp = client.get("/readyz")
    return resp.status_code, resp.json()


def _pg_check(body: dict[str, object]) -> dict[str, object]:
    checks = body["checks"]
    assert isinstance(checks, list)
    result: dict[str, object] = next(c for c in checks if c["name"] == "postgres")
    return result


def test_migrations_gate_readiness(database_url: str) -> None:
    cfg = alembic_config(ALEMBIC_INI, database_url)
    cfg.attributes["configure_logger"] = False

    # 1. Fresh database: reachable but unmigrated -> not ready.
    status, body = _readyz(database_url)
    assert status == 503
    assert "migrations not applied" in str(_pg_check(body)["detail"])

    # 2. Migrated to head -> ready (degraded only because Redis/Redpanda are absent).
    command.upgrade(cfg, "head")
    status, body = _readyz(database_url)
    assert status == 200, body
    assert body["status"] == "degraded"
    assert _pg_check(body)["detail"] == f"revision={expected_head(ALEMBIC_INI)}"

    # 3. Downgrade/upgrade round trip works and readiness follows the schema.
    command.downgrade(cfg, "base")
    status, _ = _readyz(database_url)
    assert status == 503
    command.upgrade(cfg, "head")
    status, _ = _readyz(database_url)
    assert status == 200
