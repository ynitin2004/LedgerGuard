"""Readiness probes.

Semantics (docs/ARCHITECTURE.md, "Health and readiness"):
- *Required* probes gate readiness. Only Postgres is required: nothing can be served
  correctly without it, and its schema must be at the migration head the code expects.
- *Optional* probes (Redis, Redpanda) are reported but never fail readiness, because
  the design degrades around them (plan A8: Redis -> Postgres fallback; A9: a broker
  outage is absorbed by the outbox).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

ProbeFn = Callable[[], Awaitable[str | None]]
"""A probe returns optional detail on success and raises on failure."""

ReadinessStatus = Literal["ready", "degraded", "not_ready"]


@dataclass(frozen=True, slots=True)
class Probe:
    name: str
    check: ProbeFn
    required: bool


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    ok: bool
    required: bool
    latency_ms: float
    detail: str | None


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    status: ReadinessStatus
    checks: list[ProbeResult]


async def _run(probe: Probe, timeout_s: float) -> ProbeResult:
    started = time.perf_counter()
    ok = False
    detail: str | None
    try:
        detail = await asyncio.wait_for(probe.check(), timeout=timeout_s)
        ok = True
    except TimeoutError:
        detail = f"timeout after {timeout_s}s"
    except Exception as exc:  # report, never propagate: readiness must always answer
        detail = f"{type(exc).__name__}: {exc}"[:300]
    latency = round((time.perf_counter() - started) * 1000, 2)
    return ProbeResult(probe.name, ok, probe.required, latency, detail)


async def evaluate(probes: list[Probe], timeout_s: float) -> ReadinessReport:
    results = list(await asyncio.gather(*(_run(p, timeout_s) for p in probes)))
    status: ReadinessStatus
    if any(r.required and not r.ok for r in results):
        status = "not_ready"
    elif any(not r.ok for r in results):
        status = "degraded"
    else:
        status = "ready"
    return ReadinessReport(status, results)


def postgres_probe(engine: AsyncEngine, expected_revision: str | None) -> Probe:
    async def check() -> str | None:
        async with engine.connect() as conn:
            try:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                current = result.scalar_one_or_none()
            except ProgrammingError as exc:
                raise RuntimeError("migrations not applied (no alembic_version table)") from exc
        if expected_revision is not None and current != expected_revision:
            raise RuntimeError(f"schema at {current!r}, code expects {expected_revision!r}")
        return f"revision={current}"

    return Probe("postgres", check, required=True)


def redis_probe(client: Redis) -> Probe:
    async def check() -> str | None:
        await client.ping()
        return None

    return Probe("redis", check, required=False)


def kafka_probe(bootstrap_servers: str, timeout_s: float) -> Probe:
    def _list_topics() -> str:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient({"bootstrap.servers": bootstrap_servers, "log_level": 0})
        md = admin.list_topics(timeout=timeout_s)
        return f"brokers={len(md.brokers)}"

    async def check() -> str | None:
        return await asyncio.to_thread(_list_topics)

    return Probe("redpanda", check, required=False)
