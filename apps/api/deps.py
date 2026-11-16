"""FastAPI dependency providers.

Resources are created once in the app lifespan and stored on ``app.state``;
dependencies only hand them out. This is the composition root's seam: tests
replace what is on ``app.state`` instead of patching modules.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from apps.api.problems import URN, ProblemError
from ledgerguard.application.api_clients import CachingAuthenticator
from ledgerguard.application.ports import ApiClient, UnitOfWorkFactory
from ledgerguard.application.transactions import (
    CreateTransaction,
    GetTransaction,
    ListDecisions,
)
from ledgerguard.config import Settings
from ledgerguard.infrastructure.health import Probe
from ledgerguard.infrastructure.redis import RedisRateLimiter
from ledgerguard.observability.logging import get_logger

log = get_logger(__name__)

# auto_error=False: a missing key becomes our own problem+json 401, not FastAPI's 403.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_probes(request: Request) -> list[Probe]:
    probes: list[Probe] = request.app.state.probes
    return probes


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    factory: UnitOfWorkFactory = request.app.state.uow_factory
    return factory


async def get_current_client(
    request: Request,
    raw_key: Annotated[str | None, Security(api_key_header)],
) -> ApiClient:
    client = None
    if raw_key:  # no lookup at all when the header is absent
        authenticator: CachingAuthenticator = request.app.state.authenticator
        client = await authenticator(raw_key)
    if client is None:
        # Same answer for missing, unknown and revoked keys.
        raise ProblemError(
            401,
            URN + "unauthenticated",
            "Missing or invalid API key",
            detail="Send a valid key in the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return client


async def rate_limited_client(
    request: Request, client: Annotated[ApiClient, Depends(get_current_client)]
) -> ApiClient:
    """The authenticated client, after its per-second quota (fails open, plan A8)."""
    limiter: RedisRateLimiter = request.app.state.rate_limiter
    try:
        allowed = await limiter.allow(client.id)
    except Exception as exc:  # Redis down: never block traffic on the limiter
        log.warning("rate_limit.unavailable", error=type(exc).__name__)
        return client
    if not allowed:
        raise ProblemError(
            429,
            URN + "rate-limited",
            "Too many requests",
            detail=f"Limit is {limiter.limit} requests per second per API client.",
            headers={"Retry-After": "1"},
        )
    return client


def get_create_transaction(request: Request) -> CreateTransaction:
    use_case: CreateTransaction = request.app.state.create_transaction
    return use_case


def get_get_transaction(request: Request) -> GetTransaction:
    use_case: GetTransaction = request.app.state.get_transaction
    return use_case


def get_list_decisions(request: Request) -> ListDecisions:
    use_case: ListDecisions = request.app.state.list_decisions
    return use_case
