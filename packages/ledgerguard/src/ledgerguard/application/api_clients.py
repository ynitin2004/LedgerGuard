"""API client authentication and registration (plan A1)."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from ledgerguard.application.ports import ApiClient, UnitOfWorkFactory
from ledgerguard.domain.api_keys import generate_api_key, hash_api_key


async def authenticate(uow_factory: UnitOfWorkFactory, raw_key: str) -> ApiClient | None:
    """Return the active client owning ``raw_key``, or None (unknown or revoked)."""
    async with uow_factory() as uow:
        return await uow.api_clients.get_active_by_key_hash(hash_api_key(raw_key))


class CachingAuthenticator:
    """``authenticate`` with a small in-process cache of successful lookups.

    Saves a database transaction per request on the hot path. Trade-off: a revoked key
    keeps working for up to ``ttl_s`` seconds in each API process (documented in
    SECURITY.md). Only hits are cached, keyed by the key's hash (never the raw key), and
    the cache is bounded, so random invalid keys cannot grow it.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ttl_s: float,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow_factory = uow_factory
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, ApiClient]] = OrderedDict()

    async def __call__(self, raw_key: str) -> ApiClient | None:
        if self._ttl_s <= 0:
            return await authenticate(self._uow_factory, raw_key)
        key_hash = hash_api_key(raw_key)
        now = self._clock()
        hit = self._entries.get(key_hash)
        if hit is not None and hit[0] > now:
            self._entries.move_to_end(key_hash)
            return hit[1]
        async with self._uow_factory() as uow:
            client = await uow.api_clients.get_active_by_key_hash(key_hash)
        if client is None:
            self._entries.pop(key_hash, None)
            return None
        self._entries[key_hash] = (now + self._ttl_s, client)
        self._entries.move_to_end(key_hash)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return client


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
