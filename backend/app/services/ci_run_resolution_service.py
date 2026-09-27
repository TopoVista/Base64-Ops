"""Deterministic, repository-scoped selection of failed GitHub Actions runs."""

from __future__ import annotations

from pydantic import BaseModel

from app.git.actions import GitHubActionsAdapter, WorkflowRunSummary
from app.services.ci_request_intent import CIRequestIntent


class ResolvedCIRun(BaseModel):
    run_id: int
    workflow_name: str | None = None
    workflow_path: str | None = None
    branch: str | None = None
    head_sha: str
    event: str | None = None
    html_url: str | None = None
    selection_reason: str


class CIRunResolutionService:
    def __init__(self, actions: GitHubActionsAdapter | None = None) -> None:
        self.actions = actions or GitHubActionsAdapter()

    async def resolve(self, *, user_id: str, repository_id: str, intent: CIRequestIntent) -> ResolvedCIRun | None:
        if intent.explicit_run_id is not None:
            run = await self.actions.get_workflow_run(
                user_id=user_id, repository_id=repository_id, run_id=intent.explicit_run_id
            )
            if run is None or run.repository_id != repository_id:
                return None
            if intent.is_ci_investigation and run.conclusion != "failure":
                return None
            return self._resolved(run, "explicit_run_id")

        if intent.pr_number is not None:
            pr_head = await self.actions.get_pull_request_head(
                user_id=user_id, repository_id=repository_id, pull_number=intent.pr_number
            )
            if pr_head is None:
                return None
            head_sha, head_branch = pr_head
            runs = await self.actions.list_workflow_runs(
                user_id=user_id, repository_id=repository_id, branch=head_branch
            )
            failed = [
                run
                for run in runs
                if run.repository_id == repository_id and run.conclusion == "failure" and run.head_sha == head_sha
            ]
            if intent.workflow_name:
                failed = [run for run in failed if run.workflow_name == intent.workflow_name]
            return self._resolved(failed[0], "pull_request_head") if failed else None

        runs = await self.actions.list_workflow_runs(user_id=user_id, repository_id=repository_id, branch=intent.branch)
        failed = [run for run in runs if run.repository_id == repository_id and run.conclusion == "failure"]
        if intent.workflow_name:
            failed = [run for run in failed if run.workflow_name == intent.workflow_name]
        if not failed:
            return None
        return self._resolved(failed[0], "latest_failed_run")

    @staticmethod
    def _resolved(run: WorkflowRunSummary, reason: str) -> ResolvedCIRun:
        return ResolvedCIRun(
            run_id=run.id,
            workflow_name=run.workflow_name,
            workflow_path=run.workflow_path,
            branch=run.branch,
            head_sha=run.head_sha,
            event=run.event,
            html_url=run.html_url,
            selection_reason=reason,
        )
