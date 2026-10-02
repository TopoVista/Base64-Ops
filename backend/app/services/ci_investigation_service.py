"""Gather GitHub Actions failure context as bounded, normal evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.git.actions import CIFailureCategory, CIInvestigationContext, GitHubActionsAdapter
from app.git.change_provider import GitChangeProvider
from app.services.ci_applicability import assess_workflow_path
from app.services.evidence_service import EvidenceService
from app.services.patch_engine import PatchSafetyError, RepositoryPathPolicy
from app.services.workspace_service import WorkspaceService

_PATH_HINT = re.compile(r"(?<![\w.-])((?:\.?/?[\w.-]+/)+[\w.-]+(?:\:\d+)?)")


class CIInvestigationService:
    """Read-only CI context orchestration; diagnosis remains in the graph."""

    def __init__(
        self,
        actions: GitHubActionsAdapter | None = None,
        workspace: WorkspaceService | None = None,
        changes: GitChangeProvider | None = None,
    ) -> None:
        self.actions = actions or GitHubActionsAdapter()
        self.evidence = EvidenceService()
        self.workspace = workspace or WorkspaceService()
        self.changes = changes or GitChangeProvider()

    async def investigate_actions_run(
        self,
        *,
        user_id: str,
        repository_id: str,
        run_id: int,
        session: dict[str, Any],
        graph_run_id: str,
        current_head_sha: str | None = None,
        repo_path: Path | None = None,
    ) -> CIInvestigationContext:
        run = await self.actions.get_workflow_run(
            user_id=user_id, repository_id=repository_id, run_id=run_id
        )
        if run is None:
            return CIInvestigationContext(
                run_id=run_id, head_sha="", incomplete=True, limitations=["Workflow run was unavailable."]
            )

        limitations: list[str] = []
        try:
            workflow_path = await self.actions.resolve_workflow_path(
                user_id=user_id, repository_id=repository_id, run=run
            )
        except Exception:
            workflow_path = None
            limitations.append("Unable to identify the workflow definition for the selected run.")
        if workflow_path is None:
            if not limitations:
                limitations.append("Unable to identify the workflow definition for the selected run.")
        elif workflow_path != run.workflow_path:
            run = run.model_copy(update={"workflow_path": workflow_path})
        jobs = await self.actions.get_failed_jobs(
            user_id=user_id, repository_id=repository_id, run_id=run.id
        )
        sources: list[dict[str, Any]] = []
        categories = set()
        path_hints: list[str] = []
        for job in jobs:
            try:
                excerpts = await self.actions.get_job_logs(
                    user_id=user_id, repository_id=repository_id, run_id=run.id, job=job
                )
            except Exception:
                limitations.append(f"GitHub did not permit access to logs for job {job.name}.")
                continue
            for excerpt in excerpts:
                categories.update(excerpt.categories)
                if repo_path:
                    path_hints.extend(_extract_safe_path_hints(excerpt.excerpt, repo_path))
                sources.append(
                    {
                        "kind": "ci_log",
                        "source": f"github-actions/run/{run.id}/job/{job.id}",
                        "excerpt": excerpt.excerpt,
                            "lineStart": excerpt.line_start,
                            "lineEnd": excerpt.line_end,
                            "sourceTimestamp": job.completed_at,
                        "metadata": {
                            "workflow_run_id": run.id,
                            "workflow_name": run.workflow_name,
                            "workflow_path": run.workflow_path,
                            "job_id": job.id,
                            "job_name": job.name,
                            "failed_step": job.failed_step,
                            "head_sha": run.head_sha,
                            "branch": run.branch,
                            "conclusion": job.conclusion,
                            "html_url": job.html_url,
                            "categories": [str(category) for category in excerpt.categories],
                            "truncated": excerpt.truncated,
                            "untrusted": True,
                        },
                    }
                )
        records = await self.evidence.record_sources(
            user_id=user_id,
            session=session,
            run_id=graph_run_id,
            sources=sources,
            commit_sha=run.head_sha,
        )
        workflow_evidence_ids: list[str] = []
        changed_file_evidence_ids: list[str] = []
        relevant_paths: list[str] = []
        base_sha: str | None = None
        applicability: str | None = None
        applicability_reason: str | None = None
        applicability_result: dict[str, Any] | None = None

        # A workflow is repository evidence at a precise revision, never an
        # executable instruction. Historical and current versions are persisted
        # independently to prevent accidental SHA conflation.
        if run.workflow_path and repo_path:
            failed_workflow = self.workspace.read_file_at_ref(repo_path, run.workflow_path, run.head_sha)
            if failed_workflow is None:
                limitations.append("The workflow file was unavailable at the failed-run commit.")
            else:
                historical_records = await self.evidence.record_sources(
                    user_id=user_id,
                    session=session,
                    run_id=graph_run_id,
                    sources=[
                        {
                            "kind": "repo",
                            "source": run.workflow_path,
                            "excerpt": failed_workflow,
                            "metadata": {
                                "state": "failed_run",
                                "untrusted": True,
                                "workflow_run_id": run.id,
                                "workflow_name": run.workflow_name,
                                "head_sha": run.head_sha,
                            },
                        }
                    ],
                    commit_sha=run.head_sha,
                )
                workflow_evidence_ids.extend(record["id"] for record in historical_records)

                current_workflow: str | None = None
                if current_head_sha == run.head_sha:
                    current_workflow = failed_workflow
                elif current_head_sha:
                    current_workflow = self.workspace.read_file_at_ref(
                        repo_path, run.workflow_path, current_head_sha
                    )
                    if current_workflow is None:
                        limitations.append("The current workflow version was unavailable for comparison.")
                    else:
                        current_records = await self.evidence.record_sources(
                            user_id=user_id,
                            session=session,
                            run_id=graph_run_id,
                            sources=[
                                {
                                    "kind": "repo",
                                    "source": run.workflow_path,
                                    "excerpt": current_workflow,
                                    "metadata": {
                                        "state": "current",
                                        "untrusted": True,
                                        "workflow_run_id": run.id,
                                        "workflow_name": run.workflow_name,
                                        "head_sha": current_head_sha,
                                    },
                                }
                            ],
                            commit_sha=current_head_sha,
                        )
                        workflow_evidence_ids.extend(record["id"] for record in current_records)

                result = assess_workflow_path(
                    failed_workflow=failed_workflow,
                    current_workflow=current_workflow,
                    failed_run_sha=run.head_sha,
                    current_head_sha=current_head_sha,
                    evidence_ids=workflow_evidence_ids,
                )
                applicability = str(result.status)
                applicability_reason = result.reason
                applicability_result = result.model_dump(mode="json")
        elif run.workflow_path:
            limitations.append("No repository workspace was available to inspect the workflow at the failed commit.")

        # Compare the failed commit with its actual parent where it is locally
        # available. A missing parent is an explicit limitation, never a guess.
        # Path hints from bounded failure excerpts are still useful without a
        # compare result: the code workbench should focus a file explicitly
        # named by the failing test/stack trace, never an arbitrary first file.
        selected_paths: list[str] = []
        if repo_path:
            parent_sha = self.workspace.parent_commit(repo_path, run.head_sha)
            if parent_sha:
                base_sha = parent_sha
                try:
                    change_result = await self.changes.changed_paths(
                        base_sha=parent_sha,
                        head_sha=run.head_sha,
                        repo_path=repo_path,
                        user_id=user_id,
                        repository_id=repository_id,
                    )
                except Exception:
                    limitations.append("Unable to determine the changed files for the failed run.")
                    change_result = None
                if change_result is not None:
                    selected_paths = _select_relevant_paths(change_result.changed_paths, categories, run.workflow_path)
                    if not change_result.complete and change_result.warning:
                        limitations.append(change_result.warning)
            else:
                limitations.append("Unable to determine the parent commit for the failed run.")
            relevant_paths = _prioritize_relevant_paths(
                path_hints=path_hints,
                changed_paths=selected_paths,
                categories=categories,
                workflow_path=run.workflow_path,
            )
            if relevant_paths:
                changed_sources: list[dict[str, Any]] = []
                for path in relevant_paths:
                    text = self.workspace.read_file_at_ref(repo_path, path, run.head_sha)
                    if text is not None:
                        changed_sources.append(
                            {
                                "kind": "repo",
                                "source": path,
                                "excerpt": text,
                                "metadata": {
                                    "state": "failed_run",
                                    "untrusted": True,
                                    "workflow_run_id": run.id,
                                    "head_sha": run.head_sha,
                                    "correlation": "log_path" if path in path_hints else "changed_path",
                                },
                            }
                        )
                changed_records = await self.evidence.record_sources(
                    user_id=user_id,
                    session=session,
                    run_id=graph_run_id,
                    sources=changed_sources,
                    commit_sha=run.head_sha,
                )
                changed_file_evidence_ids = [record["id"] for record in changed_records]
        return CIInvestigationContext(
            run_id=run.id,
            workflow_name=run.workflow_name,
            workflow_path=run.workflow_path,
            event=run.event,
            branch=run.branch,
            head_sha=run.head_sha,
            base_sha=base_sha,
            failed_jobs=jobs,
            evidence_ids=[record["id"] for record in records],
            workflow_evidence_ids=workflow_evidence_ids,
            changed_file_evidence_ids=changed_file_evidence_ids,
            failure_categories=sorted(categories, key=str),
            relevant_paths=relevant_paths,
            current_head_sha=current_head_sha,
            run_is_historical=bool(current_head_sha and current_head_sha != run.head_sha),
            applicability=applicability,
            applicability_reason=applicability_reason,
            applicability_result=applicability_result,
            incomplete=bool(limitations),
            limitations=limitations,
        )


def _select_relevant_paths(
    paths: list[str], categories: set[CIFailureCategory], workflow_path: str | None
) -> list[str]:
    """Conservatively select changed files for correlation, never causation."""
    selected: list[str] = []
    dependency_names = {
        "requirements.txt",
        "pyproject.toml",
        "poetry.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }
    for path in paths:
        normalized = path.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        name = normalized.rsplit("/", 1)[-1]
        include = normalized == workflow_path
        if CIFailureCategory.DEPENDENCY_FAILURE in categories:
            include = include or name in dependency_names or normalized.endswith((".py", ".ts", ".tsx", ".js"))
        if CIFailureCategory.TEST_FAILURE in categories:
            include = include or "/tests/" in f"/{normalized}" or normalized.endswith((".py", ".ts", ".tsx", ".js"))
        if CIFailureCategory.MISSING_FILE in categories:
            include = include or name in dependency_names or normalized.startswith(("backend/", "client/", "server/"))
        if CIFailureCategory.TYPECHECK_FAILURE in categories:
            include = include or normalized.endswith((".ts", ".tsx", "tsconfig.json", "package.json"))
        if include and normalized not in selected:
            selected.append(normalized)
        if len(selected) >= 8:
            break
    return selected


def _prioritize_relevant_paths(
    *,
    path_hints: list[str],
    changed_paths: list[str],
    categories: set[CIFailureCategory],
    workflow_path: str | None,
) -> list[str]:
    """Rank concrete log paths before broad changed-file correlation.

    This is deterministic navigation assistance, not a causal claim.  For a
    failed test, its test path is the safest first editor target. For a missing
    file/configuration failure, the workflow is the first target because it
    commonly establishes the failed working directory or command.
    """
    candidates = [*path_hints, *changed_paths]
    if workflow_path and CIFailureCategory.MISSING_FILE in categories:
        candidates.append(workflow_path)
    normalized = list(dict.fromkeys([
        path.replace("\\", "/").lstrip("./") for path in candidates
    ]))

    def score(path: str) -> tuple[int, int, str]:
        name = path.rsplit("/", 1)[-1]
        value = 50
        if path in path_hints:
            value -= 30
        if CIFailureCategory.TEST_FAILURE in categories and ("/tests/" in f"/{path}" or name.startswith("test_")):
            value -= 35
        if CIFailureCategory.MISSING_FILE in categories and path == workflow_path:
            value -= 40
        dependency_files = {
            "requirements.txt", "pyproject.toml", "package.json", "package-lock.json",
            "poetry.lock", "pnpm-lock.yaml", "yarn.lock",
        }
        if CIFailureCategory.DEPENDENCY_FAILURE in categories and name in dependency_files:
            value -= 25
        if path == workflow_path:
            value -= 8
        return (value, len(path), path)

    return sorted(normalized, key=score)[:8]


def _extract_safe_path_hints(text: str, repo_path: Path) -> list[str]:
    """Accept only existing, repository-relative log paths through the shared policy."""
    policy = RepositoryPathPolicy()
    hints: list[str] = []
    for match in _PATH_HINT.finditer(text):
        candidate = match.group(1).split(":", 1)[0].replace("\\", "/")
        while candidate.startswith("./"):
            candidate = candidate[2:]
        try:
            policy.validate(repo_path, candidate)
        except PatchSafetyError:
            continue
        if candidate not in hints:
            hints.append(candidate)
        if len(hints) >= 8:
            break
    return hints
