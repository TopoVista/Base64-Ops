import pytest

from app.api.routes import system
from app.core.config import Settings


@pytest.mark.asyncio
async def test_disabled_execution_is_reported_as_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(system, "get_settings", lambda: Settings(execution_mode="disabled"))
    monkeypatch.setattr(system.DockerSandboxRuntime, "is_available", lambda: False)

    result = await system.get_system_capabilities()

    assert result["execution"]["runtime"] == "unavailable"
    assert result["execution"]["active_validation"] is False
    assert result["execution"]["unavailable_reason"]


@pytest.mark.asyncio
async def test_missing_docker_sandbox_is_not_advertised_as_local_execution(monkeypatch) -> None:
    monkeypatch.setattr(system, "get_settings", lambda: Settings(execution_mode="docker-sandbox"))
    monkeypatch.setattr(system.DockerSandboxRuntime, "is_available", lambda: False)

    result = await system.get_system_capabilities()

    assert result["execution"]["runtime"] == "unavailable"
    assert result["execution"]["active_validation"] is False


@pytest.mark.asyncio
async def test_restricted_local_execution_is_only_advertised_outside_production(monkeypatch) -> None:
    monkeypatch.setattr(system, "get_settings", lambda: Settings(execution_mode="restricted-local"))
    monkeypatch.setattr(system.DockerSandboxRuntime, "is_available", lambda: False)

    result = await system.get_system_capabilities()

    assert result["execution"]["runtime"] == "restricted-local"
    assert result["execution"]["active_validation"] is True
