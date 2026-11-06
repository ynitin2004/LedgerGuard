"""Every error is RFC 9457 problem+json carrying the correlation ID (no database needed)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app
from tests.helpers import make_settings


def _client() -> TestClient:
    app = create_app(make_settings(), probes=[])

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    # Unhandled errors must become a 500 response, not propagate into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_unknown_route_is_404_problem() -> None:
    with _client() as client:
        resp = client.get("/nope", headers={"X-Correlation-ID": "abc-123"})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers.get_list("x-correlation-id") == ["abc-123"]  # exactly once
    assert resp.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Not Found",
        "instance": "/nope",
        "correlation_id": "abc-123",
    }


def test_wrong_method_is_405_problem() -> None:
    with _client() as client:
        resp = client.delete("/healthz")
    assert resp.status_code == 405
    assert resp.json()["title"] == "Method Not Allowed"


def test_unhandled_exception_is_500_problem_without_internals() -> None:
    with _client() as client:
        resp = client.get("/boom", headers={"X-Correlation-ID": "trace-me"})
    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/problem+json"
    # The outer error handler still returns the correlation ID (header and body).
    assert resp.headers["x-correlation-id"] == "trace-me"
    problem = resp.json()
    assert problem["correlation_id"] == "trace-me"
    assert problem["title"] == "Internal Server Error"
    assert "secret" not in resp.text


def test_missing_api_key_is_401_before_any_database_access() -> None:
    # probes=[] means no engine exists: a 401 proves auth fails fast without the DB.
    with _client() as client:
        resp = client.post("/v1/transactions", json={})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "ApiKey"
    assert resp.json()["type"] == "urn:ledgerguard:problem:unauthenticated"


def test_openapi_documents_problem_responses() -> None:
    with _client() as client:
        spec = client.get("/openapi.json").json()
    post = spec["paths"]["/v1/transactions"]["post"]
    assert {"201", "401", "409", "422"} <= set(post["responses"])
    params = {p["name"] for p in post["parameters"]}
    assert "Idempotency-Key" in params
    assert "APIKeyHeader" in spec["components"]["securitySchemes"]
