"""Redis adapters: client factory, velocity windows (plan A8) and rate limiting.

Redis is never required for correctness. Every caller treats an exception as "Redis
unavailable" and degrades (Postgres fallback, fail-open rate limit), so the client
uses short timeouts and NO automatic retries: redis-py 8 defaults to 10 retries with
backoff, which would turn a Redis outage into slow requests instead of a fast fallback.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from ledgerguard.risk.features import VELOCITY_1H, VELOCITY_5M, VelocityCounts

# Member that marks a window as complete (rebuilt from Postgres). Score 0 sits far
# outside any time window, so it is never counted or trimmed.
READY_MEMBER = "!ready"
WINDOW_TTL_S = int(VELOCITY_1H.total_seconds()) + 300


def create_redis(url: str, timeout_s: float) -> Redis:
    return Redis.from_url(
        url,
        socket_timeout=timeout_s,
        socket_connect_timeout=timeout_s,
        retry=Retry(NoBackoff(), 0),
        health_check_interval=0,
    )


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _us(at: datetime) -> int:
    """Exact integer epoch microseconds (Postgres precision; < 2**53, exact as a score)."""
    return (at - _EPOCH) // timedelta(microseconds=1)


# Atomically: if the window is complete, trim entries older than 1 h and return both
# counts; otherwise return nil (caller falls back to Postgres and rebuilds). All bounds
# arrive precomputed as integer strings: no arithmetic in Lua (its number formatting
# would round 16-digit timestamps).
# ARGV: 1 = now, 2 = now - 5 min, 3 = now - 1 h, 4 = ready member.
_COUNTS_LUA = """
if not redis.call('ZSCORE', KEYS[1], ARGV[4]) then return false end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '(0', '(' .. ARGV[3])
return {
  redis.call('ZCOUNT', KEYS[1], '(' .. ARGV[2], ARGV[1]),
  redis.call('ZCOUNT', KEYS[1], '(' .. ARGV[3], ARGV[1])
}
"""


class RedisVelocityStore:
    """Per-customer sorted set of payment times (score = epoch ms, member = txn id).

    Completeness protocol: a window is trusted only if it contains READY_MEMBER, which
    is written by ``rebuild`` together with every payment Postgres knows about in the
    last hour. ``record`` always adds (creating a not-yet-ready key if needed), so a
    payment committed while a rebuild is in flight is never lost: rebuild and record
    are both unions. Window semantics match ``velocity_from_history`` exactly:
    ``now - window < created_at <= now``.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._counts = redis.register_script(_COUNTS_LUA)

    @staticmethod
    def key(client_id: UUID, customer_token: str) -> str:
        return f"lg:vel:{client_id}:{customer_token}"

    async def counts(
        self, client_id: UUID, customer_token: str, now: datetime
    ) -> VelocityCounts | None:
        result = await self._counts(
            keys=[self.key(client_id, customer_token)],
            args=[
                str(_us(now)),
                str(_us(now - VELOCITY_5M)),
                str(_us(now - VELOCITY_1H)),
                READY_MEMBER,
            ],
        )
        if result is None:
            return None
        last_5m, last_1h = (int(v) for v in result)
        return VelocityCounts(last_5m=last_5m, last_1h=last_1h)

    async def rebuild(
        self, client_id: UUID, customer_token: str, entries: list[tuple[str, datetime]]
    ) -> None:
        key = self.key(client_id, customer_token)
        mapping: dict[str, int] = {txn_id: _us(at) for txn_id, at in entries}
        mapping[READY_MEMBER] = 0
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zadd(key, mapping)
            pipe.expire(key, WINDOW_TTL_S)
            await pipe.execute()

    async def record(
        self, client_id: UUID, customer_token: str, transaction_id: str, at: datetime
    ) -> None:
        key = self.key(client_id, customer_token)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {transaction_id: _us(at)})
            pipe.expire(key, WINDOW_TTL_S)
            await pipe.execute()

    async def invalidate(self, client_id: UUID, customer_token: str) -> None:
        await self._redis.delete(self.key(client_id, customer_token))


class RedisRateLimiter:
    """Fixed one-second window per API client. Fails open (plan A8): callers catch errors."""

    def __init__(self, redis: Redis, limit_per_second: int) -> None:
        self._redis = redis
        self._limit = limit_per_second

    @property
    def limit(self) -> int:
        return self._limit

    async def allow(self, client_id: UUID) -> bool:
        if self._limit <= 0:
            return True
        key = f"lg:rl:{client_id}:{int(time.time())}"
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, 2)
            count, _ = await pipe.execute()
        return int(count) <= self._limit
