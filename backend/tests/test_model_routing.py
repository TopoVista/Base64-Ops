import pytest

from app.agent import graph as agent_graph_module
from app.agent.graph import AgentGraph
from app.core.config import Settings
from app.services import model_routing_service
from app.services.model_routing_service import ModelRoutingService


def test_legacy_or_unknown_repositories_default_to_private() -> None:
    assert ModelRoutingService.classify_repository({}) == "private"
    assert ModelRoutingService.classify_repository({"repositoryVisibility": "public"}) == "public"
    assert ModelRoutingService.classify_repository({"repositoryClassification": "regulated"}) == "regulated"
    assert ModelRoutingService.classify_repository({"repositoryClassification": "browser-selected"}) == "private"


def test_restricted_policy_never_falls_back_to_hosted_provider() -> None:
    router = ModelRoutingService(
        Settings(
            openai_api_key="hosted-key-is-configured",
            model_routing_restricted_route="deterministic_only",
        )
    )

    route = router.resolve("restricted")

    assert route.kind == "deterministic_only"
    assert route.available is False
    assert route.data_boundary == "no_external_model_egress"
    assert route.provenance()["provider"] == "none"


def test_regulated_route_uses_configured_sovereign_compatible_provider() -> None:
    router = ModelRoutingService(
        Settings(
            model_routing_regulated_route="sovereign_openai_compatible",
            model_sovereign_base_url="https://models.customer.example/v1",
            model_sovereign_api_key="customer-gateway-key",
            model_sovereign_model="customer-secure-model",
        )
    )

    route = router.resolve("regulated")

    assert route.kind == "sovereign_openai_compatible"
    assert route.available is True
    assert route.provider == "customer_managed_openai_compatible"
    assert route.provenance() == {
        "classification": "regulated",
        "route": "sovereign_openai_compatible",
        "provider": "customer_managed_openai_compatible",
        "model": "customer-secure-model",
        "dataBoundary": "customer_or_local_boundary",
        "available": True,
        "reason": "configured_sovereign_route",
    }


def test_unconfigured_sovereign_route_fails_closed_without_hosted_fallback() -> None:
    router = ModelRoutingService(
        Settings(
            openai_api_key="hosted-key-is-configured",
            model_routing_regulated_route="sovereign_openai_compatible",
        )
    )

    route = router.resolve("regulated")

    assert route.kind == "sovereign_openai_compatible"
    assert route.available is False
    assert route.reason == "sovereign_provider_unconfigured"


@pytest.mark.asyncio
async def test_restricted_graph_response_does_not_create_a_hosted_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        openai_api_key="hosted-key-is-configured",
        model_routing_restricted_route="deterministic_only",
    )
    monkeypatch.setattr(agent_graph_module, "get_settings", lambda: settings)
    monkeypatch.setattr(model_routing_service, "get_settings", lambda: settings)

    result = await AgentGraph()._respond(
        {
            "repository_classification": "restricted",
            "prompt": "What failed?",
            "sources": [],
            "tool_result": {"output": "Deterministic evidence only."},
            "investigation": {},
        }
    )

    assert "No policy-authorized model route" in result["final"]
    assert result["model_usage"]["model_provider"] == "none"
    assert result["model_usage"]["data_boundary"] == "no_external_model_egress"
