"""Liveness and readiness endpoints.

- ``/healthz`` (liveness): the process is up and its event loop answers. It touches
  no dependency, so a database outage never makes an orchestrator restart healthy pods.
- ``/readyz`` (readiness): the instance can serve traffic correctly. 503 when a
  *required* dependency (Postgres, schema at head) fails; 200 with
  ``status="degraded"`` when only optional ones (Redis, Redpanda) fail.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from apps.api.deps import get_app_settings, get_probes
from ledgerguard.config import Settings
from ledgerguard.infrastructure.health import Probe, evaluate

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class CheckResult(BaseModel):
    name: str
    ok: bool
    required: bool
    latency_ms: float
    detail: str | None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded", "not_ready"]
    checks: list[CheckResult]


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "A required dependency failed"}},
)
async def readyz(
    response: Response,
    probes: Annotated[list[Probe], Depends(get_probes)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ReadinessResponse:
    report = await evaluate(probes, settings.readiness_timeout_s)
    if report.status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status=report.status,
        checks=[
            CheckResult(
                name=c.name,
                ok=c.ok,
                required=c.required,
                latency_ms=c.latency_ms,
                detail=c.detail,
            )
            for c in report.checks
        ],
    )
