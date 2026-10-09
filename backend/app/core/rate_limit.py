"""Small, auditable request limiter with a Redis production path.

Rate limiting deliberately lives before agent execution.  It is not a billing
mechanism: billing quotas are enforced separately against the authenticated
tenant.  This layer protects the API, GitHub connection flow, and token-heavy
agent routes from burst abuse.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimitStore(Protocol):
    async def check(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision: ...

    async def is_available(self) -> bool: ...

    async def aclose(self) -> None: ...


class InMemoryRateLimitStore:
    """Bounded sliding-window store for local development and unit tests.

    Do not use this store to horizontally scale a public API: each process has
    its own counter.  Production uses ``RedisRateLimitStore`` when REDIS_URL is
    set, with optional fail-closed behaviour if Redis becomes unavailable.
    """

    def __init__(self, max_keys: int = 10_000) -> None:
        self._max_keys = max(1, max_keys)
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events.get(key)
            if events is None:
                # A development process must not retain one deque forever for
                # every spoofed/unique client key. Eviction is deliberately
                # local-only; production always uses Redis TTL-backed keys.
                if len(self._events) >= self._max_keys:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry = max(1, int(events[0] + window_seconds - now) + 1)
                return RateLimitDecision(False, 0, retry)
            events.append(now)
            return RateLimitDecision(True, max(0, limit - len(events)), 0)

    async def is_available(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class RedisRateLimitStore:
    """Atomic Redis sorted-set sliding-window limiter.

    Importing redis is deferred so contributors can run deterministic tests
    without a local Redis server.  ``redis`` is still a production dependency.
    """

    _LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, 0, math.max(1, math.ceil((tonumber(oldest[2]) + window - now) / 1000))}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, window)
return {1, limit - count - 1, 0}
"""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from redis.asyncio import Redis  # type: ignore[import-not-found]

            self._client = Redis.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def check(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        client = await self._get_client()
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{time.monotonic_ns()}"
        raw = await client.eval(self._LUA, 1, key, now_ms, window_seconds * 1000, limit, member)
        return RateLimitDecision(bool(int(raw[0])), int(raw[1]), int(raw[2]))

    async def is_available(self) -> bool:
        client = await self._get_client()
        return bool(await client.ping())

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def rate_limit_key(*, client_ip: str, scope: str) -> str:
    """Avoid persisting raw client IP addresses in an external cache key."""

    digest = sha256(client_ip.encode("utf-8")).hexdigest()[:24]
    return f"base64:rl:{scope}:{digest}"
