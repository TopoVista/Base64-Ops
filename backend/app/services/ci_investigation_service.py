"""Gather GitHub Actions failure context as bounded, normal evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.git.actions import (
    CIFailureCategory,
    CIInvestigationContext,
    CIWorkflowHistory,
    GitHubActionsAdapter,
    WorkflowRunSummary,
)
from app.git.change_provider import GitChangeProvider
from app.services.ci_applicability import assess_workflow_path
from app.services.evidence_service import EvidenceService
from app.services.patch_engine import PatchSafetyError, RepositoryPathPolicy
from app.services.workspace_service import WorkspaceService

_PATH_HINT = re.compile(r"(?<![\w.-])((?:\.?/?[\w.-]+/)+[\w.-]+(?:\:\d+)?)")
_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})


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
        failure_text: list[str] = []
        failed_test_identifiers: list[str] = []
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
                failure_text.append(excerpt.excerpt)
                for identifier in excerpt.failed_test_identifiers:
                    if identifier not in failed_test_identifiers:
                        failed_test_identifiers.append(identifier)
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
                            "failed_test_identifiers": excerpt.failed_test_identifiers,
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
        history, history_evidence_ids, history_limitations = await self._collect_run_history(
            user_id=user_id,
            repository_id=repository_id,
            run=run,
            session=session,
            graph_run_id=graph_run_id,
        )
        limitations.extend(history_limitations)
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
                if (
                    history
                    and history.last_successful_head_sha
                    and history.last_successful_head_sha != run.head_sha
                ):
                    prior_workflow = self.workspace.read_file_at_ref(
                        repo_path, run.workflow_path, history.last_successful_head_sha
                    )
                    workflow_comparison = compare_workflow_versions(
                        failed_workflow=failed_workflow, prior_successful_workflow=prior_workflow
                    )
                    history = history.model_copy(update=workflow_comparison)
                    if prior_workflow is None:
                        limitations.append(
                            "The workflow file at the last comparable successful run was unavailable."
                        )
                    else:
                        prior_records = await self.evidence.record_sources(
                            user_id=user_id,
                            session=session,
                            run_id=graph_run_id,
                            sources=[
                                {
                                    "kind": "repo",
                                    "source": run.workflow_path,
                                    "excerpt": prior_workflow,
                                    "metadata": {
                                        "state": "last_successful_run",
                                        "untrusted": True,
                                        "workflow_run_id": history.last_successful_run_id,
                                        "head_sha": history.last_successful_head_sha,
                                        "comparison": "workflow_prior_success",
                                    },
                                }
                            ],
                            commit_sha=history.last_successful_head_sha,
                        )
                        workflow_evidence_ids.extend(record["id"] for record in prior_records)
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
            # A missing package is often exposed by an unchanged manifest: a
            # source import can be added without its corresponding dependency
            # declaration. Include existing, conventional manifests as
            # repository evidence for dependency failures, but do not infer
            # that any one manifest caused the failure.
            if CIFailureCategory.DEPENDENCY_FAILURE in categories:
                dependency_surface = _dependency_surface("\n".join(failure_text))
                selected_paths = _filter_dependency_paths(selected_paths, dependency_surface)
                for path in _existing_dependency_manifests(
                    self.workspace, repo_path, run.head_sha, dependency_surface
                ):
                    if path not in selected_paths:
                        selected_paths.append(path)
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
            history_evidence_ids=history_evidence_ids,
            workflow_evidence_ids=workflow_evidence_ids,
            changed_file_evidence_ids=changed_file_evidence_ids,
            failure_categories=sorted(categories, key=str),
            failed_test_identifiers=failed_test_identifiers,
            relevant_paths=relevant_paths,
            current_head_sha=current_head_sha,
            run_is_historical=bool(current_head_sha and current_head_sha != run.head_sha),
            applicability=applicability,
            applicability_reason=applicability_reason,
            applicability_result=applicability_result,
            history=history,
            incomplete=bool(limitations),
            limitations=limitations,
        )

    async def _collect_run_history(
        self,
        *,
        user_id: str,
        repository_id: str,
        run: WorkflowRunSummary,
        session: dict[str, Any],
        graph_run_id: str,
    ) -> tuple[CIWorkflowHistory | None, list[str], list[str]]:
        """Persist a compact, read-only comparison of bounded Actions metadata."""

        try:
            runs = await self.actions.list_workflow_runs(
                user_id=user_id, repository_id=repository_id, branch=run.branch
            )
        except Exception:
            return None, [], ["Unable to retrieve comparable workflow-run history."]
        history = compare_workflow_history(failed_run=run, runs=runs)
        if not history.compared_run_ids:
            return history, [], []
        excerpt = _history_excerpt(history)
        records = await self.evidence.record_sources(
            user_id=user_id,
            session=session,
            run_id=graph_run_id,
            sources=[
                {
                    "kind": "tool_result",
                    "source": f"github-actions/history/run/{run.id}",
                    "excerpt": excerpt,
                    "metadata": {
                        "untrusted": True,
                        "workflow_run_id": run.id,
                        "head_sha": run.head_sha,
                        "assessment": history.assessment,
                        "compared_run_ids": history.compared_run_ids,
                        "last_successful_run_id": history.last_successful_run_id,
                        "first_observed_failure_run_id": history.first_observed_failure_run_id,
                        "same_sha_failure_count": history.same_sha_failure_count,
                        "same_sha_success_count": history.same_sha_success_count,
                    },
                }
            ],
            commit_sha=run.head_sha,
        )
        return history, [record["id"] for record in records], []


def compare_workflow_history(
    *, failed_run: WorkflowRunSummary, runs: list[WorkflowRunSummary]
) -> CIWorkflowHistory:
    """Describe a bounded set of comparable workflow runs without inferring cause.

    GitHub run IDs alone do not establish time ordering, so missing timestamps
    intentionally degrade to an explicit insufficient-history result.
    """

    comparable = [
        run
        for run in runs
        if _same_workflow(failed_run, run)
        and (not failed_run.branch or not run.branch or run.branch == failed_run.branch)
        and run.created_at is not None
    ]
    if failed_run.created_at is None:
        return CIWorkflowHistory(
            assessment="insufficient_history",
            limitation="The failed run did not include a creation timestamp for chronological comparison.",
        )
    ordered = sorted(comparable, key=lambda item: (item.created_at, item.id))
    observed = [item for item in ordered if item.created_at <= failed_run.created_at]
    compared_ids = [item.id for item in observed[:30]]
    same_sha = [item for item in observed if item.head_sha == failed_run.head_sha]
    same_sha_failures = sum(item.conclusion in _FAILED_CONCLUSIONS for item in same_sha)
    same_sha_successes = sum(item.conclusion == "success" for item in same_sha)
    prior_successes = [
        item
        for item in observed
        if item.id != failed_run.id and item.created_at < failed_run.created_at and item.conclusion == "success"
    ]
    failures = [item for item in observed if item.conclusion in _FAILED_CONCLUSIONS]
    last_success = prior_successes[-1] if prior_successes else None
    first_failure = failures[0] if failures else None
    if same_sha_failures >= 2 and same_sha_successes >= 1:
        assessment = "mixed_outcomes_same_sha"
    elif same_sha_failures >= 2:
        assessment = "repeated_failure_same_sha"
    elif last_success and last_success.head_sha != failed_run.head_sha:
        assessment = "failure_after_prior_success"
    else:
        assessment = "insufficient_history"
    return CIWorkflowHistory(
        compared_run_ids=compared_ids,
        last_successful_run_id=last_success.id if last_success else None,
        last_successful_head_sha=last_success.head_sha if last_success else None,
        first_observed_failure_run_id=first_failure.id if first_failure else None,
        same_sha_failure_count=same_sha_failures,
        same_sha_success_count=same_sha_successes,
        assessment=assessment,
        limitation=None if compared_ids else "No comparable workflow history was available in the bounded window.",
    )


def compare_workflow_versions(
    *, failed_workflow: str, prior_successful_workflow: str | None
) -> dict[str, bool | str | None]:
    """Describe an exact workflow-file comparison without asserting cause."""

    if prior_successful_workflow is None:
        return {
            "workflow_changed_since_last_success": None,
            "workflow_comparison_note": "The last successful workflow file was unavailable for comparison.",
        }
    if prior_successful_workflow == failed_workflow:
        return {
            "workflow_changed_since_last_success": False,
            "workflow_comparison_note": "The workflow file matched the last comparable successful run.",
        }
    return {
        "workflow_changed_since_last_success": True,
        "workflow_comparison_note": (
            "The workflow file differs from the last comparable successful run; "
            "this is correlation evidence, not proof of cause."
        ),
    }


def _same_workflow(expected: WorkflowRunSummary, candidate: WorkflowRunSummary) -> bool:
    if expected.workflow_id is not None and candidate.workflow_id is not None:
        return expected.workflow_id == candidate.workflow_id
    if expected.workflow_path and candidate.workflow_path:
        return expected.workflow_path == candidate.workflow_path
    return bool(expected.workflow_name and expected.workflow_name == candidate.workflow_name)


def _history_excerpt(history: CIWorkflowHistory) -> str:
    """Create compact human-readable metadata evidence, never a diagnosis claim."""

    pieces = [
        f"Comparable workflow runs: {len(history.compared_run_ids)}.",
        f"Assessment: {history.assessment.replace('_', ' ')}.",
        f"Same-SHA outcomes: {history.same_sha_failure_count} failure(s), "
        f"{history.same_sha_success_count} success(es).",
    ]
    if history.last_successful_run_id is not None:
        pieces.append(f"Last prior successful run: #{history.last_successful_run_id}.")
    if history.workflow_comparison_note:
        pieces.append(history.workflow_comparison_note)
    if history.first_observed_failure_run_id is not None:
        pieces.append(f"First observed failed run in the bounded window: #{history.first_observed_failure_run_id}.")
    if history.limitation:
        pieces.append(f"Limitation: {history.limitation}")
    pieces.append("This comparison is correlation evidence, not proof of causation or flakiness.")
    return " ".join(pieces)


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


def _dependency_surface(text: str) -> str:
    """Classify the package surface only when the bounded log is explicit."""

    normalized = text.lower()
    python_markers = (
        "modulenotfounderror",
        "importerror",
        "traceback (most recent call last)",
        ".py:",
    )
    if any(marker in normalized for marker in python_markers):
        return "python"
    if any(marker in normalized for marker in ("npm err", "node_modules", "module_not_found", "ts[0-9]{4}")):
        return "node"
    return "unknown"


def _filter_dependency_paths(paths: list[str], surface: str) -> list[str]:
    """Avoid presenting a Node manifest for an explicit Python import error."""

    node_manifests = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
    python_manifests = {"requirements.txt", "pyproject.toml", "poetry.lock"}
    if surface == "python":
        return [path for path in paths if path.rsplit("/", 1)[-1] not in node_manifests]
    if surface == "node":
        return [path for path in paths if path.rsplit("/", 1)[-1] not in python_manifests]
    return paths


def _existing_dependency_manifests(
    workspace: WorkspaceService, repo_path: Path, ref: str, surface: str = "unknown"
) -> list[str]:
    """Return bounded, conventional dependency files available at ``ref``.

    This is a correlation aid for a deterministic dependency-failure signal,
    not a package-manager guess. Reading through ``read_file_at_ref`` keeps
    failed-run provenance intact and avoids silently substituting current
    branch files.
    """

    python_candidates = (
        "backend/requirements.txt",
        "requirements.txt",
        "backend/pyproject.toml",
        "pyproject.toml",
        "backend/poetry.lock",
        "poetry.lock",
    )
    node_candidates = (
        "client/package.json",
        "package.json",
        "client/package-lock.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    )
    if surface == "python":
        candidates = python_candidates
    elif surface == "node":
        candidates = node_candidates
    else:
        candidates = python_candidates + node_candidates
    return [
        path
        for path in candidates
        if workspace.read_file_at_ref(repo_path, path, ref) is not None
    ]


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
            value -= 30 if name == "requirements.txt" else 25
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
