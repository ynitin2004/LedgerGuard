"""API client authentication and registration (plan A1)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from ledgerguard.application.ports import ApiClient, UnitOfWorkFactory
from ledgerguard.domain.api_keys import generate_api_key, hash_api_key


async def authenticate(uow_factory: UnitOfWorkFactory, raw_key: str) -> ApiClient | None:
    """Return the active client owning ``raw_key``, or None (unknown or revoked)."""
    async with uow_factory() as uow:
        return await uow.api_clients.get_active_by_key_hash(hash_api_key(raw_key))


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    client: ApiClient
    raw_key: str  # shown once; only its hash is stored


async def register_api_client(
    uow_factory: UnitOfWorkFactory, *, name: str, tenant_id: UUID | None = None
) -> IssuedApiKey:
    """Create a client and its key. In the MVP each client is its own tenant.

    Needs INSERT on ``api_clients``, which the runtime app role does not have: this
    is an admin operation (scripts/create_api_client.py; admin API in M5).
    """
    raw_key = generate_api_key()
    async with uow_factory() as uow:
        client = await uow.api_clients.add(
            tenant_id=tenant_id or uuid4(), name=name, key_hash=hash_api_key(raw_key)
        )
        await uow.commit()
    return IssuedApiKey(client=client, raw_key=raw_key)
