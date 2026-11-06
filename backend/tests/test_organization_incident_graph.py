import pytest

from app.services import organization_incident_graph_service as graph_module
from app.services.organization_incident_graph_service import (
    OrganizationIncidentGraphService,
    _node_id,
    correlation_basis,
)


def test_synthetic_multi_repo_incidents_are_correlated_not_called_causal() -> None:
    service = OrganizationIncidentGraphService()
    first = service.incident_signals(
        incident={"workflowPath": ".github/workflows/build.yml", "failureCategories": ["test_failure"]},
        context={"capsule_inputs": {"runnerLabels": ["ubuntu-latest"]}},
    )
    second = service.incident_signals(
        incident={"workflowPath": ".github/workflows/build.yml", "failureCategories": ["test_failure"]},
        context={"capsule_inputs": {"runnerLabels": ["ubuntu-latest"]}},
    )

    assert correlation_basis(first, second) == ["same_workflow", "same_runner_label", "same_failure_category"]
    assert "causal" not in correlation_basis(first, second)


def test_unrelated_incidents_do_not_receive_a_correlation_edge() -> None:
    service = OrganizationIncidentGraphService()
    first = service.incident_signals(
        incident={"workflowPath": ".github/workflows/test.yml", "failureCategories": ["test_failure"]},
        context={"capsule_inputs": {"runnerLabels": ["ubuntu-latest"]}},
    )
    second = service.incident_signals(
        incident={"workflowPath": ".github/workflows/release.yml", "failureCategories": ["build_failure"]},
        context={"capsule_inputs": {"runnerLabels": ["windows-latest"]}},
    )

    assert correlation_basis(first, second) == []


def test_tenant_identity_prevents_cross_tenant_node_collisions() -> None:
    first = _node_id("tenant_a", "repository", "github:42")
    second = _node_id("tenant_b", "repository", "github:42")

    assert first != second


@pytest.mark.asyncio
async def test_graph_persists_cross_repo_correlation_inside_one_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        def __init__(self, rows):  # noqa: ANN001
            self.rows = rows

        def sort(self, *_args):  # noqa: ANN002, ANN201
            return self

        def limit(self, _limit):  # noqa: ANN001, ANN201
            return self

        async def to_list(self, _limit):  # noqa: ANN001, ANN201
            return [dict(row) for row in self.rows]

    class Collection:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        async def update_one(self, query, update, *, upsert):  # noqa: ANN001, ANN201
            row = next((item for item in self.rows if item["_id"] == query["_id"]), None)
            if row is None:
                assert upsert
                row = {**update.get("$setOnInsert", {})}
                self.rows.append(row)
            row.update(update["$set"])

        def find(self, query, *_args):  # noqa: ANN002, ANN201
            rows = [
                row
                for row in self.rows
                if row.get("tenantId") == query.get("tenantId")
                and row.get("nodeType") == query.get("nodeType")
                and row.get("_id") != (query.get("_id") or {}).get("$ne")
            ]
            return Cursor(rows)

    database = type(
        "Database",
        (),
        {"organization_graph_nodes": Collection(), "organization_graph_edges": Collection()},
    )()
    monkeypatch.setattr(graph_module, "get_db", lambda: database)
    service = OrganizationIncidentGraphService()
    context = {
        "capsule_inputs": {"runnerLabels": ["ubuntu-latest"]},
        "failure_categories": ["test_failure"],
    }
    await service.ingest_incident(
        tenant_id="tenant_a",
        incident={
            "_id": "inc_a",
            "repositoryId": "repo_a",
            "workflowPath": ".github/workflows/build.yml",
            "headSha": "a" * 40,
            "evidenceIds": ["evd_a"],
        },
        context=context,
    )
    result = await service.ingest_incident(
        tenant_id="tenant_a",
        incident={
            "_id": "inc_b",
            "repositoryId": "repo_b",
            "workflowPath": ".github/workflows/build.yml",
            "headSha": "b" * 40,
            "evidenceIds": ["evd_b"],
        },
        context=context,
    )

    correlations = [edge for edge in database.organization_graph_edges.rows if edge["relation"] == "correlated_with"]
    assert result["correlations"] == 1
    assert correlations[0]["certainty"] == "correlated"
    assert correlations[0]["basis"] == ["same_workflow", "same_runner_label", "same_failure_category"]
    assert {node["tenantId"] for node in database.organization_graph_nodes.rows} == {"tenant_a"}
