"""FastAPI dependency providers.

Resources are created once in the app lifespan and stored on ``app.state``;
dependencies only hand them out. This is the composition root's seam: tests
replace what is on ``app.state`` instead of patching modules.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from apps.api.problems import URN, ProblemError
from ledgerguard.application.api_clients import authenticate
from ledgerguard.application.ports import ApiClient, UnitOfWorkFactory
from ledgerguard.application.transactions import CreateTransaction, GetTransaction
from ledgerguard.config import Settings
from ledgerguard.infrastructure.health import Probe

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
    # No database round trip when the header is absent.
    client = await authenticate(get_uow_factory(request), raw_key) if raw_key else None
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


def get_create_transaction(request: Request) -> CreateTransaction:
    use_case: CreateTransaction = request.app.state.create_transaction
    return use_case


def get_get_transaction(request: Request) -> GetTransaction:
    use_case: GetTransaction = request.app.state.get_transaction
    return use_case
