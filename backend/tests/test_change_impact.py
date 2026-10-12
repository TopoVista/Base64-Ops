from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.intelligence.graph.builder import EngineeringGraphBuilder
from app.intelligence.impact.models import ChangeImpactResponse, ChangeImpactStatus
from app.intelligence.impact.service import ChangeImpactService
from app.intelligence.impact.store import ChangeImpactStore
from app.schemas.delivery import DeliveryPlan, ProposedFileChange


def _snapshot(tmp_path: Path, *, sha: str = "a" * 40):
    return EngineeringGraphBuilder().build(
        repo_path=tmp_path,
        user_id="user_a",
        session_id="session_a",
        repository_id="https://github.com/acme/fixture",
        commit_sha=sha,
        branch="main",
        files=sorted((item for item in tmp_path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()),
    )


def _plan(*, sha: str = "a" * 40, path: str = "core.py") -> DeliveryPlan:
    return DeliveryPlan(
        id="dpl_impact",
        user_id="user_a",
        session_id="session_a",
        run_id="run_a",
        repository_id="https://github.com/acme/fixture",
        base_branch="main",
        base_sha=sha,
        title="Update core behavior",
        rationale="Fixture only",
        files=[
            ProposedFileChange(
                path=path,
                change_type="modify",
                original_hash="before",
                proposed_hash="after",
                unified_diff="--- a/core.py\n+++ b/core.py\n",
            )
        ],
        validation_steps=[],
        risk_level="medium",
        created_at=datetime.now(UTC),
    )


def test_impact_analysis_finds_reverse_dependents_and_related_tests(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "service.py").write_text("from core import value\n", encoding="utf-8")
    (tmp_path / "test_service.py").write_text(
        "from service import value\n\ndef test_value():\n    assert value == 1\n", encoding="utf-8"
    )

    result = ChangeImpactService().analyze(
        user_id="user_a",
        session_id="session_a",
        repository_id="https://github.com/acme/fixture",
        plan=_plan(),
        snapshot=_snapshot(tmp_path),
    )

    assert result.status is ChangeImpactStatus.AVAILABLE
    assert [item.path for item in result.directly_affected] == ["service.py"]
    assert [item.path for item in result.indirectly_affected] == ["test_service.py"]
    assert [item.name for item in result.related_tests] == ["test_value"]
    assert "not proof" in result.directly_affected[0].explanation
    assert all(item.node_type.value != "repository" for item in result.directly_affected)


def test_impact_analysis_refuses_to_mix_plan_and_graph_revisions(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("value = 1\n", encoding="utf-8")

    result = ChangeImpactService().analyze(
        user_id="user_a",
        session_id="session_a",
        repository_id="https://github.com/acme/fixture",
        plan=_plan(sha="b" * 40),
        snapshot=_snapshot(tmp_path, sha="a" * 40),
    )

    assert result.status is ChangeImpactStatus.UNAVAILABLE
    assert not result.directly_affected
    assert any("different revision" in limitation for limitation in result.limitations)


def test_impact_analysis_reports_unmapped_files_without_inventing_dependents(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("value = 1\n", encoding="utf-8")

    result = ChangeImpactService().analyze(
        user_id="user_a",
        session_id="session_a",
        repository_id="https://github.com/acme/fixture",
        plan=_plan(path="missing.py"),
        snapshot=_snapshot(tmp_path),
    )

    assert result.status is ChangeImpactStatus.UNAVAILABLE
    assert result.unmapped_paths == ["missing.py"]
    assert not result.directly_affected
    assert any("not represented" in limitation for limitation in result.limitations)


class _Collection:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.indexes: list[tuple] = []

    async def create_index(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.indexes.append((args, kwargs))

    async def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        if any(all(row.get(key) == value for key, value in query.items()) for row in self.rows):
            return
        if upsert:
            self.rows.append(dict(update["$setOnInsert"]))

    async def find_one(self, query: dict):
        row = next((item for item in self.rows if all(item.get(key) == value for key, value in query.items())), None)
        return dict(row) if row else None


async def test_impact_store_is_idempotent_and_tenant_scoped(tmp_path: Path, monkeypatch) -> None:
    from app.intelligence.impact import store as impact_store_module

    (tmp_path / "core.py").write_text("value = 1\n", encoding="utf-8")
    analysis = ChangeImpactService().analyze(
        user_id="user_a",
        session_id="session_a",
        repository_id="https://github.com/acme/fixture",
        plan=_plan(),
        snapshot=_snapshot(tmp_path),
    )
    collection = _Collection()
    monkeypatch.setattr(impact_store_module, "get_db", lambda: SimpleNamespace(change_impact_analyses=collection))
    store = ChangeImpactStore()

    await store.ensure_indexes()
    first = await store.save(analysis)
    second = await store.save(analysis)

    assert first.id == second.id
    assert len(collection.rows) == 1
    assert await store.by_id(user_id="user_b", session_id="session_a", analysis_id=analysis.id) is None
    assert len(collection.indexes) == 2
    public = ChangeImpactResponse.from_analysis(first).model_dump()
    assert "user_id" not in public
    assert "repository_id" not in public
