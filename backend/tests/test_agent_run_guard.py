import pytest
from fastapi import HTTPException

from app.core import agent_run_guard
from app.core.agent_run_guard import AgentRunGuard, InMemoryLeaseStore
from app.core.config import Settings


async def test_agent_run_guard_allows_only_one_concurrent_run_for_a_tenant() -> None:
    guard = AgentRunGuard(Settings(), InMemoryLeaseStore())
    first = await guard.acquire("user-a")

    with pytest.raises(HTTPException) as blocked:
        await guard.acquire("user-a")

    assert blocked.value.status_code == 429
    await guard.release(first)
    second = await guard.acquire("user-a")
    await guard.release(second)


async def test_agent_run_guard_isolated_between_tenants() -> None:
    guard = AgentRunGuard(Settings(), InMemoryLeaseStore())
    first = await guard.acquire("user-a")
    second = await guard.acquire("user-b")

    await guard.release(first)
    await guard.release(second)


async def test_in_memory_lease_expires_after_its_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr(agent_run_guard.time, "monotonic", lambda: now)
    store = InMemoryLeaseStore()

    assert await store.acquire("tenant", "first", 10)
    now = 109.9
    assert not await store.acquire("tenant", "second", 10)
    now = 110.0
    assert await store.acquire("tenant", "second", 10)
