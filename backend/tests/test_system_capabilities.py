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


@pytest.mark.asyncio
async def test_candidate_verification_never_advertises_unconfigured_remote_ci(monkeypatch) -> None:
    monkeypatch.setattr(system, "get_settings", lambda: Settings())
    monkeypatch.setattr(system.DockerSandboxRuntime, "is_available", lambda: False)

    result = await system.get_system_capabilities()

    verification = result["github"]["candidate_verification"]
    assert verification["implemented"] is True
    assert verification["mode"] == "static_only"
    assert verification["configured"] is False
    assert verification["accessible"] is False
    assert verification["unavailable_reason"]

    reporting = result["github"]["incident_check_reporting"]
    assert reporting["implemented"] is True
    assert reporting["configured"] is False
    assert reporting["accessible"] is False
