"""Read-only typed GitHub Actions adapter with bounded, redacted log evidence."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.redaction_service import RedactionService


class CIFailureCategory(StrEnum):
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    TYPECHECK_FAILURE = "typecheck_failure"
    LINT_FAILURE = "lint_failure"
    DEPENDENCY_FAILURE = "dependency_failure"
    CONFIGURATION_FAILURE = "configuration_failure"
    MISSING_FILE = "missing_file"
    MISSING_ENVIRONMENT = "missing_environment"
    PERMISSION_FAILURE = "permission_failure"
    NETWORK_FAILURE = "network_failure"
    DEPLOYMENT_FAILURE = "deployment_failure"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class WorkflowRunSummary(BaseModel):
    id: int
    repository_id: str
    workflow_id: int | None = None
    workflow_name: str | None = None
    workflow_path: str | None = None
    event: str | None = None
    branch: str | None = None
    head_sha: str
    status: str | None = None
    conclusion: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    html_url: str | None = None


class WorkflowJobSummary(BaseModel):
    id: int
    run_id: int
    name: str
    status: str | None = None
    conclusion: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_step: str | None = None
    html_url: str | None = None


class CILogExcerpt(BaseModel):
    run_id: int
    job_id: int
    job_name: str
    excerpt: str
    line_start: int | None = None
    line_end: int | None = None
    source_timestamp: datetime | None = None
    truncated: bool = False
    categories: list[CIFailureCategory] = Field(default_factory=list)


class CIInvestigationContext(BaseModel):
    run_id: int
    workflow_name: str | None = None
    workflow_path: str | None = None
    event: str | None = None
    branch: str | None = None
    head_sha: str
    base_sha: str | None = None
    current_head_sha: str | None = None
    run_is_historical: bool = False
    failed_jobs: list[WorkflowJobSummary] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    workflow_evidence_ids: list[str] = Field(default_factory=list)
    changed_file_evidence_ids: list[str] = Field(default_factory=list)
    failure_categories: list[CIFailureCategory] = Field(default_factory=list)
    relevant_paths: list[str] = Field(default_factory=list)
    applicability: str | None = None
    applicability_reason: str | None = None
    applicability_result: dict[str, Any] | None = None
    incomplete: bool = False
    limitations: list[str] = Field(default_factory=list)


_SIGNAL = re.compile(
    "|".join(
        [
            "ERROR", "FAILED", "FAILURE", "Exception", "Traceback", "exit code",
            "npm ERR!", "ImportError", "TypeError", "SyntaxError", "command not found",
            "No such file", "permission denied", "connection refused", "timeout",
        ]
    ),
    re.I,
)
_CATEGORY_RULES = (
    (r"pytest|test .* failed|assertionerror", CIFailureCategory.TEST_FAILURE),
    (r"build failed|build error|vite build|docker build", CIFailureCategory.BUILD_FAILURE),
    (r"ts\d{4}|typecheck|type error", CIFailureCategory.TYPECHECK_FAILURE),
    (r"eslint|ruff|lint", CIFailureCategory.LINT_FAILURE),
    (r"module not found|importerror|modulenotfounderror|npm err!", CIFailureCategory.DEPENDENCY_FAILURE),
    (r"no such file|enoent|working-directory", CIFailureCategory.MISSING_FILE),
    (r"environment variable|is required|missing env", CIFailureCategory.MISSING_ENVIRONMENT),
    (r"permission denied|forbidden", CIFailureCategory.PERMISSION_FAILURE),
    (r"connection refused|network|econnreset", CIFailureCategory.NETWORK_FAILURE),
    (r"timeout|timed out", CIFailureCategory.TIMEOUT),
)


def extract_failure_excerpts(*, text: str, run_id: int, job: WorkflowJobSummary, truncated: bool) -> list[CILogExcerpt]:
    """Extract redacted diagnostic windows. Matches are leads, not root-cause proof."""
    settings = get_settings()
    lines = RedactionService().redact(text).splitlines()
    excerpts: list[CILogExcerpt] = []
    for index, line in enumerate(lines):
        if not _SIGNAL.search(line):
            continue
        start, end = max(0, index - 3), min(len(lines), index + 8)
        value = "\n".join(lines[start:end])[: settings.ci_log_max_excerpt_bytes]
        categories = [category for pattern, category in _CATEGORY_RULES if re.search(pattern, value, re.I)]
        excerpts.append(
            CILogExcerpt(
                run_id=run_id,
                job_id=job.id,
                job_name=job.name,
                excerpt=value,
                line_start=start + 1,
                line_end=end,
                truncated=truncated or len(value.encode()) >= settings.ci_log_max_excerpt_bytes,
                categories=categories or [CIFailureCategory.UNKNOWN],
            )
        )
        if len(excerpts) >= settings.ci_log_max_excerpts_per_job:
            break
    return excerpts


class GitHubActionsAdapter:
    """Actions boundary backed exclusively by the existing GitHub OAuth service."""

    @staticmethod
    def _repo(repository_id: str) -> str | None:
        found = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", repository_id)
        return found.group(1) if found else (repository_id if re.fullmatch(r"[^/]+/[^/]+", repository_id) else None)

    async def list_workflow_runs(
        self, *, user_id: str, repository_id: str, branch: str | None = None
    ) -> list[WorkflowRunSummary]:
        from app.services.github_service import github_api

        repo = self._repo(repository_id)
        if not repo:
            return []
        params: dict[str, Any] = {"per_page": 30}
        if branch:
            params["branch"] = branch
        data = await github_api(user_id, "GET", f"/repos/{repo}/actions/runs", params=params)
        return [self._run(item, repository_id) for item in (data or {}).get("workflow_runs", [])]

    async def get_workflow_run(self, *, user_id: str, repository_id: str, run_id: int) -> WorkflowRunSummary | None:
        from app.services.github_service import github_api

        repo = self._repo(repository_id)
        if not repo:
            return None
        data = await github_api(user_id, "GET", f"/repos/{repo}/actions/runs/{run_id}")
        return self._run(data, repository_id) if data else None

    async def resolve_workflow_path(
        self, *, user_id: str, repository_id: str, run: WorkflowRunSummary
    ) -> str | None:
        """Resolve a run's workflow path only when GitHub metadata identifies it unambiguously."""
        if run.workflow_path:
            return run.workflow_path
        from app.services.github_service import github_api

        repo = self._repo(repository_id)
        if not repo:
            return None
        data = await github_api(user_id, "GET", f"/repos/{repo}/actions/workflows", params={"per_page": 100})
        matches = []
        for workflow in (data or {}).get("workflows", []):
            if run.workflow_id is not None and workflow.get("id") == run.workflow_id:
                matches.append(workflow)
            elif run.workflow_name and workflow.get("name") == run.workflow_name:
                matches.append(workflow)
        paths = {item.get("path") for item in matches if item.get("path")}
        return next(iter(paths)) if len(paths) == 1 else None

    async def get_pull_request_head(
        self, *, user_id: str, repository_id: str, pull_number: int
    ) -> tuple[str, str | None] | None:
        """Return a PR head SHA/ref through the existing read-only GitHub boundary."""
        from app.services.github_service import github_api

        repo = self._repo(repository_id)
        if not repo:
            return None
        data = await github_api(user_id, "GET", f"/repos/{repo}/pulls/{pull_number}")
        head = (data or {}).get("head") or {}
        sha = head.get("sha")
        return (sha, head.get("ref")) if sha else None

    async def list_jobs(self, *, user_id: str, repository_id: str, run_id: int) -> list[WorkflowJobSummary]:
        from app.services.github_service import github_api

        repo = self._repo(repository_id)
        if not repo:
            return []
        data = await github_api(
            user_id,
            "GET",
            f"/repos/{repo}/actions/runs/{run_id}/jobs",
            params={"per_page": get_settings().ci_log_max_jobs_per_run},
        )
        return [self._job(item, run_id) for item in (data or {}).get("jobs", [])]

    async def get_failed_jobs(self, **kwargs: Any) -> list[WorkflowJobSummary]:
        return [job for job in await self.list_jobs(**kwargs) if job.conclusion == "failure"]

    async def get_job_logs(
        self, *, user_id: str, repository_id: str, run_id: int, job: WorkflowJobSummary
    ) -> list[CILogExcerpt]:
        from app.services.github_service import github_api_bytes

        repo = self._repo(repository_id)
        if not repo:
            return []
        payload, truncated = await github_api_bytes(
            user_id, f"/repos/{repo}/actions/jobs/{job.id}/logs", max_bytes=get_settings().ci_log_max_download_bytes
        )
        return extract_failure_excerpts(
            text=payload.decode("utf-8", errors="replace"), run_id=run_id, job=job, truncated=truncated
        )

    @staticmethod
    def _run(data: dict[str, Any], repository_id: str) -> WorkflowRunSummary:
        return WorkflowRunSummary(
            id=data["id"],
            repository_id=repository_id,
            workflow_id=data.get("workflow_id"),
            workflow_name=data.get("name"),
            workflow_path=data.get("path"),
            event=data.get("event"),
            branch=data.get("head_branch"),
            head_sha=data.get("head_sha", ""),
            status=data.get("status"),
            conclusion=data.get("conclusion"),
            created_at=_parse_timestamp(data.get("created_at")),
            updated_at=_parse_timestamp(data.get("updated_at")),
            html_url=data.get("html_url"),
        )

    @staticmethod
    def _job(data: dict[str, Any], run_id: int) -> WorkflowJobSummary:
        failed = next((step.get("name") for step in data.get("steps", []) if step.get("conclusion") == "failure"), None)
        return WorkflowJobSummary(
            id=data["id"],
            run_id=run_id,
            name=data.get("name", "job"),
            status=data.get("status"),
            conclusion=data.get("conclusion"),
            started_at=_parse_timestamp(data.get("started_at")),
            completed_at=_parse_timestamp(data.get("completed_at")),
            failed_step=failed,
            html_url=data.get("html_url"),
        )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
