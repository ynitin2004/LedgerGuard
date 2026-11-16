"""CachingAuthenticator: hits are cached for the TTL; misses and revocations are not."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from ledgerguard.application.api_clients import CachingAuthenticator
from ledgerguard.application.ports import ApiClient
from ledgerguard.domain.api_keys import hash_api_key

CLIENT = ApiClient(id=uuid4(), tenant_id=uuid4(), name="c", status="active")


class FakeClients:
    def __init__(self) -> None:
        self.active: dict[str, ApiClient] = {}
        self.lookups = 0

    async def get_active_by_key_hash(self, key_hash: str) -> ApiClient | None:
        self.lookups += 1
        return self.active.get(key_hash)


class FakeUow:
    def __init__(self, clients: FakeClients) -> None:
        self.api_clients = clients

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None


def make(
    ttl: float, max_entries: int = 10
) -> tuple[CachingAuthenticator, FakeClients, list[float]]:
    clients, now = FakeClients(), [0.0]

    def factory() -> Any:
        return FakeUow(clients)

    return CachingAuthenticator(factory, ttl, max_entries, clock=lambda: now[0]), clients, now


def test_hits_are_cached_until_the_ttl_expires() -> None:
    auth, clients, now = make(ttl=10)
    clients.active[hash_api_key("k")] = CLIENT

    async def scenario() -> None:
        assert await auth("k") == CLIENT
        assert await auth("k") == CLIENT
        assert clients.lookups == 1
        del clients.active[hash_api_key("k")]  # revoked
        now[0] = 9.9
        assert await auth("k") == CLIENT  # still cached: bounded revocation delay
        now[0] = 10.0
        assert await auth("k") is None
        assert clients.lookups == 2

    asyncio.run(scenario())


def test_misses_are_never_cached_and_cache_is_bounded() -> None:
    auth, clients, _ = make(ttl=10, max_entries=2)
    for k in ("a", "b", "c"):
        clients.active[hash_api_key(k)] = CLIENT

    async def scenario() -> None:
        assert await auth("unknown") is None
        assert await auth("unknown") is None
        assert clients.lookups == 2
        for k in ("a", "b", "c"):
            await auth(k)
        before = clients.lookups
        await auth("a")  # evicted (least recently used)
        assert clients.lookups == before + 1

    asyncio.run(scenario())


def test_ttl_zero_disables_the_cache() -> None:
    auth, clients, _ = make(ttl=0)
    clients.active[hash_api_key("k")] = CLIENT

    async def scenario() -> None:
        await auth("k")
        await auth("k")
        assert clients.lookups == 2

    asyncio.run(scenario())
