"""Liveness/readiness semantics with injected probes (no Docker needed)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from apps.api.main import create_app
from ledgerguard.infrastructure.health import Probe
from tests.helpers import make_settings


def _probe(name: str, *, required: bool, fail: bool = False, hang: bool = False) -> Probe:
    async def check() -> str | None:
        if hang:
            await asyncio.sleep(10)
        if fail:
            raise ConnectionError(f"{name} unreachable")
        return "fine"

    return Probe(name, check, required)


def _client(*probes: Probe, timeout_s: float = 1.0) -> TestClient:
    app = create_app(make_settings(readiness_timeout_s=timeout_s), probes=list(probes))
    return TestClient(app)


def test_healthz_is_ok_even_when_every_dependency_is_down() -> None:
    with _client(_probe("postgres", required=True, fail=True)) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ready_when_all_probes_pass() -> None:
    with _client(_probe("postgres", required=True), _probe("redis", required=False)) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert {c["name"] for c in body["checks"]} == {"postgres", "redis"}


def test_readyz_503_when_required_dependency_fails() -> None:
    with _client(
        _probe("postgres", required=True, fail=True), _probe("redis", required=False)
    ) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    pg = next(c for c in body["checks"] if c["name"] == "postgres")
    assert pg["ok"] is False
    assert "unreachable" in pg["detail"]


def test_readyz_degraded_but_200_when_only_optional_dependency_fails() -> None:
    # Plan A8/A9: Redis and the broker are degradable, so they must not pull the
    # instance out of the load balancer.
    with _client(
        _probe("postgres", required=True),
        _probe("redis", required=False, fail=True),
        _probe("redpanda", required=False, fail=True),
    ) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_readyz_times_out_a_hanging_probe() -> None:
    with _client(_probe("postgres", required=True, hang=True), timeout_s=0.2) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert "timeout" in resp.json()["checks"][0]["detail"]
