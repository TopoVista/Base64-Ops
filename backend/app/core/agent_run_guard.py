"""Distributed tenant run leases that stop parallel token-spend abuse."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from fastapi import HTTPException, status

from app.core.config import Settings, get_settings


def _tenant_key(user_id: str) -> str:
    return "base64:agent-lease:" + sha256(user_id.encode("utf-8")).hexdigest()[:32]


class LeaseStore(Protocol):
    async def acquire(self, key: str, token: str, ttl_seconds: int) -> bool: ...

    async def release(self, key: str, token: str) -> None: ...


class InMemoryLeaseStore:
    """Process-local lease store for development.

    Keeping the same expiry semantics as Redis matters even locally: a worker
    crash must not leave a tenant permanently unable to start another run.
    It is intentionally not used for horizontally scaled production traffic.
    """

    def __init__(self) -> None:
        self._leases: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, token: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            expired = [lease_key for lease_key, (_, expires_at) in self._leases.items() if expires_at <= now]
            for lease_key in expired:
                self._leases.pop(lease_key, None)
            if key in self._leases:
                return False
            self._leases[key] = (token, now + max(1, ttl_seconds))
            return True

    async def release(self, key: str, token: str) -> None:
        async with self._lock:
            lease = self._leases.get(key)
            if lease and lease[0] == token:
                self._leases.pop(key, None)


class RedisLeaseStore:
    _RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from redis.asyncio import Redis  # type: ignore[import-not-found]

            self._client = Redis.from_url(self._redis_url, decode_responses=True)
        return self._client

    async def acquire(self, key: str, token: str, ttl_seconds: int) -> bool:
        client = await self._get_client()
        return bool(await client.set(key, token, nx=True, ex=ttl_seconds))

    async def release(self, key: str, token: str) -> None:
        client = await self._get_client()
        await client.eval(self._RELEASE_LUA, 1, key, token)


@dataclass(frozen=True)
class AgentRunLease:
    key: str
    token: str


class AgentRunGuard:
    """One active graph run per tenant by default.

    The configured upper bound intentionally only supports one today. Higher
    concurrency needs a Redis counter/Lua policy and measured queue capacity;
    silently treating a number greater than one as safe would be misleading.
    """

    def __init__(self, settings: Settings | None = None, store: LeaseStore | None = None) -> None:
        self._settings = settings or get_settings()
        self._store = store or (
            RedisLeaseStore(self._settings.redis_url) if self._settings.redis_url else InMemoryLeaseStore()
        )

    async def acquire(self, user_id: str) -> AgentRunLease:
        key = _tenant_key(user_id)
        token = secrets.token_urlsafe(24)
        try:
            acquired = await self._store.acquire(key, token, max(60, self._settings.agent_run_lease_seconds))
        except Exception as exc:
            if self._settings.rate_limit_fail_closed:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Agent traffic protection is temporarily unavailable. Please retry shortly.",
                ) from exc
            acquired = True
        if not acquired:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "AGENT_RUN_ALREADY_ACTIVE",
                    "message": (
                        "An agent run is already active for this account. "
                        "Wait for it to finish before starting another."
                    ),
                },
            )
        return AgentRunLease(key=key, token=token)

    async def release(self, lease: AgentRunLease) -> None:
        try:
            await self._store.release(lease.key, lease.token)
        except Exception:
            # The TTL is the fail-safe. A release failure must not overwrite a
            # successful agent result or expose Redis implementation details.
            return
