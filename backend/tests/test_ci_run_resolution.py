import httpx
import pytest

from app.agent.graph import AgentGraph
from app.git.actions import WorkflowRunSummary
from app.services.ci_request_intent import CIRequestIntent
from app.services.ci_run_resolution_service import CIRunResolutionService


class Actions:
    def __init__(self, runs):
        self.runs = runs

    async def get_workflow_run(self, *, repository_id, run_id, **_):
        return next((run for run in self.runs if run.id == run_id and run.repository_id == repository_id), None)

    async def list_workflow_runs(self, *, repository_id, branch=None, **_):
        return [run for run in self.runs if run.repository_id == repository_id and (not branch or run.branch == branch)]

    async def get_pull_request_head(self, *, repository_id, pull_number, **_):
        return ("846", "feature") if repository_id == "owner/a" and pull_number == 27 else None


def run(id, conclusion, branch="main", repo="owner/a"):
    return WorkflowRunSummary(
        id=id, repository_id=repo, workflow_name="CI", head_sha=str(id), conclusion=conclusion, branch=branch
    )


@pytest.mark.asyncio
async def test_explicit_failed_run_is_repository_scoped():
    resolver = CIRunResolutionService(Actions([run(842, "failure"), run(843, "failure", repo="owner/b")]))
    result = await resolver.resolve(
        user_id="u", repository_id="owner/a", intent=CIRequestIntent(is_ci_investigation=True, explicit_run_id=842)
    )
    assert result and result.selection_reason == "explicit_run_id"
    denied = await resolver.resolve(
        user_id="u", repository_id="owner/a", intent=CIRequestIntent(is_ci_investigation=True, explicit_run_id=843)
    )
    assert denied is None


@pytest.mark.asyncio
async def test_latest_failed_and_branch_filter():
    resolver = CIRunResolutionService(Actions([run(842, "success"), run(843, "failure", "dev"), run(845, "failure")]))
    intent = CIRequestIntent(is_ci_investigation=True, latest_failed=True, branch="main")
    result = await resolver.resolve(user_id="u", repository_id="owner/a", intent=intent)
    assert result and result.run_id == 845


@pytest.mark.asyncio
async def test_pr_run_resolution_uses_matching_repository_head_sha():
    resolver = CIRunResolutionService(Actions([run(846, "failure", "feature"), run(847, "failure", "feature")]))
    result = await resolver.resolve(
        user_id="u", repository_id="owner/a", intent=CIRequestIntent(is_ci_investigation=True, pr_number=27)
    )
    assert result and result.run_id == 846 and result.selection_reason == "pull_request_head"


@pytest.mark.asyncio
async def test_expired_github_actions_access_is_a_partial_investigation():
    """An expired Actions OAuth grant cannot abort the normal evidence graph."""

    class ExpiredResolver:
        async def resolve(self, **_):
            request = httpx.Request("GET", "https://api.github.com/repos/owner/a/actions/runs")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    graph = AgentGraph()
    graph.ci_run_resolution = ExpiredResolver()
    result = await graph._investigate_ci(
        {
            "prompt": "Why did GitHub Actions fail?",
            "user_id": "user_1",
            "repo_url": "owner/a",
            "timeline": [],
        }
    )

    assert result["ci_context"]["incomplete"] is True
    assert "GitHub access has expired" in result["ci_context"]["limitations"][0]
    assert result["timeline"][-1]["status"] == "partial"


@pytest.mark.asyncio
async def test_code_workbench_forces_proposal_not_log_selected_diagnostic():
    graph = AgentGraph()
    result = await graph._plan(
        {
            "prompt": "UNTRUSTED CI LOG: docker build failed",
            "force_delivery_plan": True,
            "timeline": [],
        }
    )

    assert result["action"]["name"] == "propose_change"
    assert result["action"]["risk"] == "approval_required"
