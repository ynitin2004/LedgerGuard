"""Correlation-ID middleware behaviour."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from ledgerguard.observability.correlation import resolve_correlation_id
from tests.helpers import make_settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(make_settings(), probes=[]))


def test_echoes_valid_incoming_id(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"X-Correlation-ID": "req-123.abc_DEF"})
    assert resp.headers["x-correlation-id"] == "req-123.abc_DEF"


def test_generates_uuid_when_missing(client: TestClient) -> None:
    resp = client.get("/healthz")
    uuid.UUID(resp.headers["x-correlation-id"])  # raises if not a UUID


@pytest.mark.parametrize(
    "raw",
    ["", "has space", "x" * 129, "inject\r\nheader", "<script>", "a;b"],
)
def test_rejects_unsafe_ids(raw: str) -> None:
    resolved = resolve_correlation_id(raw)
    assert resolved != raw
    uuid.UUID(resolved)
