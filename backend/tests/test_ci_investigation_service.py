from pathlib import Path

import pytest

from app.agent.graph import AgentGraph
from app.git.actions import CIFailureCategory, CILogExcerpt, WorkflowJobSummary, WorkflowRunSummary
from app.git.change_provider import ChangedPathsResult
from app.services.ci_investigation_service import CIInvestigationService, _extract_safe_path_hints


class FakeActions:
    async def resolve_workflow_path(self, *, run, **_kwargs):
        return run.workflow_path

    async def get_workflow_run(self, **_kwargs):
        return WorkflowRunSummary(
            id=842,
            repository_id="owner/repo",
            workflow_name="Frontend CI",
            workflow_path=".github/workflows/ci.yml",
            head_sha="failed-sha",
            conclusion="failure",
        )

    async def get_failed_jobs(self, **_kwargs):
        return [WorkflowJobSummary(id=17, run_id=842, name="frontend-build", conclusion="failure")]

    async def get_job_logs(self, **_kwargs):
        return [
            CILogExcerpt(
                run_id=842,
                job_id=17,
                job_name="frontend-build",
                excerpt="npm ERR! enoent package.json was not found",
                categories=[CIFailureCategory.MISSING_FILE],
            )
        ]


class FakeEvidence:
    def __init__(self):
        self.calls = []

    async def record_sources(self, **kwargs):
        self.calls.append(kwargs)
        return [{"id": f"evd_{len(self.calls)}_{index}"} for index, _ in enumerate(kwargs["sources"])]


class FakeWorkspace:
    def parent_commit(self, _repo_path: Path, _ref: str):
        return "base-sha"

    def read_file_at_ref(self, _repo_path: Path, path: str, ref: str):
        versions = {
            (".github/workflows/ci.yml", "failed-sha"): "working-directory: ./server",
            (".github/workflows/ci.yml", "current-sha"): "working-directory: ./client",
            ("client/package.json", "failed-sha"): '{"name": "client"}',
        }
        return versions.get((path, ref))


class FakeChanges:
    async def changed_paths(self, **_kwargs):
        return ChangedPathsResult(
            base_sha="base-sha",
            head_sha="failed-sha",
            changed_paths=[".github/workflows/ci.yml", "client/package.json"],
            source="local_git",
            complete=True,
        )


@pytest.mark.asyncio
async def test_ci_service_persists_separate_historical_current_and_changed_evidence():
    service = CIInvestigationService(actions=FakeActions(), workspace=FakeWorkspace(), changes=FakeChanges())
    evidence = FakeEvidence()
    service.evidence = evidence

    context = await service.investigate_actions_run(
        user_id="user-a",
        repository_id="owner/repo",
        run_id=842,
        session={"_id": "session-a"},
        graph_run_id="run-a",
        current_head_sha="current-sha",
        repo_path=Path("/fixture"),
    )

    assert context.head_sha == "failed-sha"
    assert context.current_head_sha == "current-sha"
    assert context.run_is_historical is True
    assert context.applicability == "historical_fixed"
    assert context.evidence_ids
    assert context.workflow_evidence_ids
    assert context.changed_file_evidence_ids
    assert "client/package.json" in context.relevant_paths
    assert [call["commit_sha"] for call in evidence.calls] == ["failed-sha", "failed-sha", "current-sha", "failed-sha"]
    assert evidence.calls[1]["sources"][0]["metadata"]["state"] == "failed_run"
    assert evidence.calls[2]["sources"][0]["metadata"]["state"] == "current"


@pytest.mark.asyncio
async def test_ci_service_continues_when_logs_are_not_permitted():
    class NoLogsActions(FakeActions):
        async def get_job_logs(self, **_kwargs):
            raise PermissionError("forbidden")

    service = CIInvestigationService(actions=NoLogsActions(), workspace=FakeWorkspace(), changes=FakeChanges())
    service.evidence = FakeEvidence()

    context = await service.investigate_actions_run(
        user_id="user-a",
        repository_id="owner/repo",
        run_id=842,
        session={"_id": "session-a"},
        graph_run_id="run-a",
        current_head_sha="current-sha",
        repo_path=Path("/fixture"),
    )

    assert context.incomplete is True
    assert context.evidence_ids == []
    assert any("did not permit access" in item for item in context.limitations)


@pytest.mark.asyncio
async def test_ci_service_marks_unknown_workflow_path_incomplete():
    class UnknownWorkflowActions(FakeActions):
        async def resolve_workflow_path(self, **_kwargs):
            return None

    service = CIInvestigationService(actions=UnknownWorkflowActions(), workspace=FakeWorkspace(), changes=FakeChanges())
    service.evidence = FakeEvidence()
    context = await service.investigate_actions_run(
        user_id="user-a",
        repository_id="owner/repo",
        run_id=842,
        session={"_id": "session-a"},
        graph_run_id="run-a",
        current_head_sha="current-sha",
        repo_path=Path("/fixture"),
    )

    assert context.incomplete is True
    assert any("Unable to identify the workflow" in item for item in context.limitations)


def test_ci_log_path_hints_use_repository_path_policy(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "auth.py").write_text("pass\n", encoding="utf-8")

    hints = _extract_safe_path_hints(
        "ERROR backend/auth.py:42\nERROR ../../etc/passwd\nERROR /root/.ssh/id_rsa\nERROR .git/config",
        tmp_path,
    )

    assert hints == ["backend/auth.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize("applicability", ["historical_fixed", "historical_needs_verification"])
async def test_historical_ci_applicability_blocks_delivery_plan(applicability: str):
    result = await AgentGraph()._generate_delivery({"ci_context": {"applicability": applicability}, "timeline": []})

    assert result["action"]["name"] == "answer_with_rag"
    assert result["action"]["risk"] == "safe"
    assert "delivery_plan" not in result
    assert "approval_id" not in result
