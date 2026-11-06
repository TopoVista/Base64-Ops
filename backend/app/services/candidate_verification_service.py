"""Exact-candidate verification contracts and GitHub App dispatch boundary.

OAuth-backed repository access remains read/delivery scoped. Candidate
verification can use only a separately configured GitHub App installation,
an explicit repository opt-in, and a separately approved immutable candidate.
Workflow YAML is never interpreted as a Base64 command.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.db.mongo import get_db
from app.git.actions import CIFailureCategory, WorkflowJobSummary, extract_failure_excerpts
from app.schemas.delivery import DeliveryPlan
from app.services.evidence_service import EvidenceService
from app.services.github_service import (
    github_app_is_configured,
    github_installation_api,
    github_installation_api_bytes,
    normalize_github_repository_url,
    validate_github_app_installation_repository,
)
from app.services.policy_authority_service import PolicyAuthorityService
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now
from app.utils.ids import new_id

_WORKFLOW_PATH = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]{1,180}\.ya?ml$")
_INSTALLATION_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_BRANCH = re.compile(r"^base64-verify/[A-Za-z0-9-]{6,80}$")
_SENSITIVE_CANDIDATE_PATH = re.compile(r"^\.github/workflows/", re.IGNORECASE)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def _diff_hash(plan: DeliveryPlan) -> str:
    return hashlib.sha256("\n".join(change.unified_diff for change in plan.files).encode()).hexdigest()


def candidate_branch_name(plan: DeliveryPlan) -> str:
    """Return a stable, bounded branch name derived only from the exact plan."""

    return f"base64-verify/{plan.id.removeprefix('dpl_')[:24]}-{_diff_hash(plan)[:12]}"


def candidate_verification_paths_are_safe(plan: DeliveryPlan) -> bool:
    """Verification must not run a candidate that changes its own workflow."""

    return all(not _SENSITIVE_CANDIDATE_PATH.match(change.path) for change in plan.files)


def verification_approval_is_bound(approval: dict[str, Any], plan: DeliveryPlan) -> bool:
    """Check the same exact binding used by the delivery approval engine."""

    if approval.get("action_type") != "dispatch_candidate_verification":
        return False
    diff_hash = _diff_hash(plan)
    if approval.get("diff_hash") != diff_hash or approval.get("delivery_plan_id") != plan.id:
        return False
    payload = {
        "repository_id": plan.repository_id,
        "base_branch": plan.base_branch,
        "base_sha": plan.base_sha,
        "delivery_plan_id": plan.id,
        "action_type": "dispatch_candidate_verification",
        "canonical_arguments": approval.get("canonical_arguments") or {},
        "diff_hash": diff_hash,
    }
    if plan.ledger_manifest_hash:
        payload["ledger_manifest_hash"] = plan.ledger_manifest_hash
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest() == approval.get("approval_hash")


def initial_candidate_verification(
    plan: DeliveryPlan,
    *,
    candidate_patch_hash: str,
    static_validation: list[dict[str, Any]] | None = None,
) -> dict:
    """Create the immutable verification contract for an exact candidate.

    The caller owns patch hashing so this small, persistence-safe module does
    not depend on the delivery service that invokes it.  The record is an
    honest statement of what was *not* remotely verified; it is never a
    substitute for an immutable GitHub check result.
    """

    validation = static_validation or []
    required_failures = [
        item
        for item in validation
        if item.get("status") in {"failed", "error"} and item.get("step_id") != "review"
    ]
    static_validation_passed = bool(validation) and not required_failures
    return {
        "schemaVersion": "candidate-verification-v1",
        "status": "static_validation_passed" if static_validation_passed else "proposed",
        "verificationLevel": "static_validation" if static_validation_passed else "not_independently_tested",
        "deliveryPlanId": plan.id,
        "baseSha": plan.base_sha,
        "candidatePatchHash": candidate_patch_hash,
        "workflowIdentity": None,
        "commands": [],
        # Keep an auditable, command-free outcome summary. Commands are owned
        # by the existing registry and are never reconstructed from this
        # candidate record or from untrusted workflow content.
        "staticValidation": [
            {
                "stepId": str(item.get("step_id") or ""),
                "status": str(item.get("status") or "unknown"),
                "summary": str(item.get("summary") or "")[:300],
                "durationMs": item.get("duration_ms") if isinstance(item.get("duration_ms"), int) else None,
            }
            for item in validation[:20]
        ],
        "createdAt": utc_now(),
        "limitations": [
            "No GitHub App verification-dispatch permission is configured.",
            "Static validation and later delivery CI observation are not targeted candidate verification.",
        ],
    }


class CandidateVerificationService:
    """Persist and dispatch explicit, App-authorized verification attempts.

    The service has no model input and never extracts commands from workflow
    YAML, logs, plans, or browser requests. It creates only the exact Git tree
    described by a DeliveryPlan, then dispatches one user-configured workflow
    after a separate approval record is accepted.
    """

    def __init__(self) -> None:
        self.redactor = RedactionService()
        self.evidence = EvidenceService()

    @staticmethod
    def _settings_identity(*, user_id: str, session_id: str) -> dict[str, str]:
        return {"userId": user_id, "sessionId": session_id}

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.candidate_verification_settings.create_index(
            [("userId", 1), ("sessionId", 1)], unique=True, name="candidate_verification_one_session"
        )
        await db.verification_runs.create_index(
            [("userId", 1), ("deliveryPlanId", 1), ("createdAt", -1)], name="verification_plan_timeline"
        )
        await db.verification_runs.create_index(
            [("repositoryId", 1), ("candidatePatchHash", 1)], name="verification_candidate_lookup"
        )

    async def configure_repository(
        self,
        *,
        user_id: str,
        session: dict[str, Any],
        enabled: bool,
        installation_id: str | None,
        workflow_path: str | None,
    ) -> dict[str, Any]:
        """Configure one reviewed App installation/workflow for a session.

        This proves App access against the session's OAuth-authorized
        repository before saving an opt-in. It does not create a branch,
        dispatch a workflow, or modify any GitHub resource.
        """

        from app.services.github_service import list_repositories

        now = utc_now()
        identity = self._settings_identity(user_id=user_id, session_id=str(session["_id"]))
        existing = await get_db().candidate_verification_settings.find_one(identity)
        if not enabled:
            document = {
                **identity,
                # Retain the server-derived repository binding for audit and
                # tenant isolation. Disabling only revokes this feature's
                # future authority; it does not erase historical runs.
                "repositoryId": str((existing or {}).get("repositoryId") or session.get("repositoryId") or ""),
                "repositoryFullName": (existing or {}).get("repositoryFullName"),
                "repositoryUrl": (existing or {}).get("repositoryUrl"),
                "enabled": False,
                "installationId": None,
                "workflowPath": None,
                "updatedAt": now,
            }
            await get_db().candidate_verification_settings.update_one(
                identity,
                {"$set": document, "$setOnInsert": {"_id": new_id("cvs_"), "createdAt": now}},
                upsert=True,
            )
            return document

        if not github_app_is_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub App candidate verification is not configured.",
            )
        # The installation identifier is deliberately not returned to the
        # browser.  A previously verified binding may therefore be retained
        # on later saves when the user changes nothing, but an initial opt-in
        # still has to provide a well-formed App installation identifier.
        resolved_installation_id = str(
            installation_id or (existing or {}).get("installationId") or ""
        )
        resolved_workflow_path = str(
            workflow_path or (existing or {}).get("workflowPath") or ""
        )
        if not _INSTALLATION_ID.fullmatch(resolved_installation_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid GitHub App installation.",
            )
        if not _WORKFLOW_PATH.fullmatch(resolved_workflow_path):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Verification workflow must be a .github/workflows/*.yml or .yaml path.",
            )

        _owner, _repository, canonical_url = normalize_github_repository_url(str(session.get("repoUrl") or ""))
        repositories = await list_repositories(user_id)
        repository = next((item for item in repositories if str(item.get("cloneUrl")) == canonical_url), None)
        repository_id = str(repository.get("id")) if isinstance(repository, dict) and repository.get("id") else ""
        if not repository_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository is unavailable to this GitHub account.",
            )
        app_repository = await validate_github_app_installation_repository(
            installation_id=resolved_installation_id, repository_id=repository_id
        )
        # Prove the reviewed workflow exists without reading/persisting its
        # YAML body. Its run commands remain repository data, never registry
        # commands or agent instructions.
        owner, name = str(app_repository["fullName"]).split("/", 1)
        await github_installation_api(
            resolved_installation_id,
            "GET",
            f"/repos/{owner}/{name}/contents/{resolved_workflow_path}",
            params={"ref": str(app_repository["defaultBranch"])},
        )
        document = {
            **identity,
            "repositoryId": repository_id,
            "repositoryFullName": app_repository["fullName"],
            "repositoryUrl": app_repository["cloneUrl"],
            "enabled": True,
            "installationId": resolved_installation_id,
            "workflowPath": resolved_workflow_path,
            "updatedAt": now,
        }
        await get_db().candidate_verification_settings.update_one(
            identity,
            {"$set": document, "$setOnInsert": {"_id": new_id("cvs_"), "createdAt": now}},
            upsert=True,
        )
        return document

    async def get_repository_settings(self, *, user_id: str, session_id: str) -> dict[str, Any] | None:
        return await get_db().candidate_verification_settings.find_one(
            self._settings_identity(user_id=user_id, session_id=session_id)
        )

    async def request_approval(self, *, user_id: str, session: dict[str, Any], plan: DeliveryPlan) -> dict[str, Any]:
        """Create an exact, separate approval for one verification attempt."""

        from app.services.delivery_service import DeliveryService

        settings = await self.get_repository_settings(user_id=user_id, session_id=str(session["_id"]))
        if not settings or not settings.get("enabled"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate verification is not enabled for this repository.",
            )
        if not github_app_is_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub App candidate verification is not configured.",
            )
        try:
            _owner, _repository, plan_repository_url = normalize_github_repository_url(plan.repository_id)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Verification plan repository identity is invalid.",
            ) from None
        if str(settings.get("repositoryUrl")) != plan_repository_url:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Verification repository identity no longer matches the plan.",
            )
        if not candidate_verification_paths_are_safe(plan):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate verification refuses patches that modify GitHub Actions workflows.",
            )
        now = utc_now()
        day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        existing_count = await get_db().verification_runs.count_documents(
            {
                "userId": user_id,
                "githubRepositoryId": str(settings["repositoryId"]),
                "createdAt": {"$gte": day_start},
            }
        )
        from app.core.config import get_settings

        if existing_count >= get_settings().github_candidate_verification_max_runs_per_day:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="The repository candidate-verification budget is exhausted for today.",
            )
        canonical_arguments = {
            "installation_id": str(settings["installationId"]),
            "workflow_path": str(settings["workflowPath"]),
            "candidate_branch": candidate_branch_name(plan),
            "candidate_patch_hash": _diff_hash(plan),
        }
        existing = await get_db().approvals.find_one(
            {
                "userId": user_id,
                "sessionId": str(session["_id"]),
                "deliveryPlanId": plan.id,
                "action_type": "dispatch_candidate_verification",
                "status": "pending",
                "canonical_arguments": canonical_arguments,
            }
        )
        if existing:
            return {"approval": existing, "reused": True}
        approval = await DeliveryService().create_approval(
            plan,
            action_type="dispatch_candidate_verification",
            canonical_arguments=canonical_arguments,
        )
        return {"approval": approval, "reused": False}

    async def dispatch_approved(
        self, *, user_id: str, approval: dict[str, Any], plan: DeliveryPlan
    ) -> dict[str, Any]:
        """Create a non-forced candidate ref and dispatch the reviewed workflow.

        The caller must atomically transition the existing exact approval to
        ``approved`` first. Any stale base ref, changed approval binding,
        configuration loss, or GitHub error becomes an observable verification
        outcome; no delivery PR is created by this method.
        """

        await PolicyAuthorityService().assert_execution_authorized(approval=approval, plan=plan)
        arguments = approval.get("canonical_arguments") or {}
        installation_id = str(arguments.get("installation_id") or "")
        workflow_path = str(arguments.get("workflow_path") or "")
        branch = str(arguments.get("candidate_branch") or "")
        expected_hash = str(arguments.get("candidate_patch_hash") or "")
        if not (
            _INSTALLATION_ID.fullmatch(installation_id)
            and _WORKFLOW_PATH.fullmatch(workflow_path)
            and _BRANCH.fullmatch(branch)
            and expected_hash == _diff_hash(plan)
            and candidate_verification_paths_are_safe(plan)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate verification approval is invalidated.",
            )
        settings = await self.get_repository_settings(user_id=user_id, session_id=plan.session_id)
        if not settings or not settings.get("enabled") or str(settings.get("installationId")) != installation_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate verification settings changed after approval.",
            )
        try:
            _owner, _repository, plan_repository_url = normalize_github_repository_url(plan.repository_id)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Verification plan repository identity is invalid.",
            ) from None
        if (
            str(settings.get("workflowPath")) != workflow_path
            or str(settings.get("repositoryUrl")) != plan_repository_url
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate verification binding no longer matches the repository.",
            )
        full_name = str(settings.get("repositoryFullName") or "")
        if full_name.count("/") != 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate verification repository binding is invalid.",
            )
        owner, repository = full_name.split("/", 1)
        run_id = new_id("vrf_")
        now = utc_now()
        run = {
            "_id": run_id,
            "id": run_id,
            "schemaVersion": "verification-run-v1",
            "userId": user_id,
            "sessionId": plan.session_id,
            "deliveryPlanId": plan.id,
            "approvalId": str(approval["id"]),
            "repositoryId": plan.repository_id,
            "githubRepositoryId": str(settings["repositoryId"]),
            "baseSha": plan.base_sha,
            "candidatePatchHash": expected_hash,
            "candidateBranch": branch,
            "workflowPath": workflow_path,
            "status": "dispatching",
            "verificationLevel": "not_started",
            "createdAt": now,
            "updatedAt": now,
            "limitations": [
                "The repository-owned workflow controls its own allowlisted commands and permissions.",
                "Dispatch confirmation is not targeted-test success or full-CI success.",
            ],
        }
        await get_db().verification_runs.insert_one(run)
        try:
            reference = await github_installation_api(
                installation_id,
                "GET",
                f"/repos/{owner}/{repository}/git/ref/heads/{quote(plan.base_branch, safe='')}",
            )
            current_sha = str(reference.get("object", {}).get("sha") or "") if isinstance(reference, dict) else ""
            if current_sha != plan.base_sha:
                return await self._terminal_failure(
                    run,
                    "stale",
                    "The repository branch changed after candidate verification was approved.",
                )
            base_commit = await github_installation_api(
                installation_id,
                "GET",
                f"/repos/{owner}/{repository}/git/commits/{plan.base_sha}",
            )
            base_tree = str(base_commit.get("tree", {}).get("sha") or "") if isinstance(base_commit, dict) else ""
            if not base_tree:
                return await self._terminal_failure(run, "failed", "GitHub did not return the candidate base tree.")
            tree_entries: list[dict[str, Any]] = []
            for change in plan.files:
                if change.change_type == "delete":
                    tree_entries.append({"path": change.path, "mode": "100644", "type": "blob", "sha": None})
                    continue
                blob = await github_installation_api(
                    installation_id,
                    "POST",
                    f"/repos/{owner}/{repository}/git/blobs",
                    json={"content": change.proposed_content or "", "encoding": "utf-8"},
                )
                blob_sha = str(blob.get("sha") or "") if isinstance(blob, dict) else ""
                if not blob_sha:
                    return await self._terminal_failure(run, "failed", "GitHub did not create a candidate blob.")
                tree_entries.append({"path": change.path, "mode": "100644", "type": "blob", "sha": blob_sha})
            tree = await github_installation_api(
                installation_id,
                "POST",
                f"/repos/{owner}/{repository}/git/trees",
                json={"base_tree": base_tree, "tree": tree_entries},
            )
            tree_sha = str(tree.get("sha") or "") if isinstance(tree, dict) else ""
            if not tree_sha:
                return await self._terminal_failure(run, "failed", "GitHub did not create a candidate tree.")
            commit = await github_installation_api(
                installation_id,
                "POST",
                f"/repos/{owner}/{repository}/git/commits",
                json={
                    "message": f"base64 verification candidate {plan.id}",
                    "tree": tree_sha,
                    "parents": [plan.base_sha],
                },
            )
            commit_sha = str(commit.get("sha") or "") if isinstance(commit, dict) else ""
            if not commit_sha:
                return await self._terminal_failure(run, "failed", "GitHub did not create a candidate commit.")
            await github_installation_api(
                installation_id,
                "POST",
                f"/repos/{owner}/{repository}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
            )
            await github_installation_api(
                installation_id,
                "POST",
                f"/repos/{owner}/{repository}/actions/workflows/{quote(workflow_path, safe='')}/dispatches",
                json={"ref": branch, "inputs": {}},
            )
            await get_db().verification_runs.update_one(
                {"_id": run_id, "userId": user_id},
                {
                    "$set": {
                        "status": "dispatched",
                        "verificationLevel": "dispatched_not_completed",
                        "candidateCommitSha": commit_sha,
                        "updatedAt": utc_now(),
                    }
                },
            )
            return {
                "status": "dispatched",
                "verificationRunId": run_id,
                "candidateBranch": branch,
                "candidateCommitSha": commit_sha,
                "verificationLevel": "dispatched_not_completed",
            }
        except HTTPException as exc:
            return await self._terminal_failure(run, "failed", exc.detail)
        except Exception:
            return await self._terminal_failure(run, "failed", "GitHub candidate verification could not be dispatched.")

    async def observe_run(self, *, user_id: str, verification_run_id: str) -> dict[str, Any]:
        """Read the terminal state of the one workflow dispatched for a candidate.

        This observer reads only workflow metadata scoped to the persisted
        verification run. It does not read arbitrary logs, execute commands,
        approve a patch, or infer full-CI health from a targeted workflow.
        """

        run = await get_db().verification_runs.find_one({"_id": verification_run_id, "userId": user_id})
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification run not found")
        if run.get("status") in {"succeeded", "failed", "stale", "inconclusive"}:
            return self._public_run_state(run)
        settings = await self.get_repository_settings(user_id=user_id, session_id=str(run["sessionId"]))
        if not settings or not settings.get("enabled"):
            return await self._observe_terminal_failure(
                run, "inconclusive", "Candidate verification settings are no longer enabled."
            )
        installation_id = str(settings.get("installationId") or "")
        full_name = str(settings.get("repositoryFullName") or "")
        if not _INSTALLATION_ID.fullmatch(installation_id) or full_name.count("/") != 1:
            return await self._observe_terminal_failure(
                run, "inconclusive", "Candidate verification binding is no longer valid."
            )
        owner, repository = full_name.split("/", 1)
        workflow_path = str(run.get("workflowPath") or "")
        branch = str(run.get("candidateBranch") or "")
        candidate_sha = str(run.get("candidateCommitSha") or "")
        if not _WORKFLOW_PATH.fullmatch(workflow_path) or not _BRANCH.fullmatch(branch) or not candidate_sha:
            return await self._observe_terminal_failure(
                run, "inconclusive", "Candidate verification record is incomplete."
            )
        try:
            data = await github_installation_api(
                installation_id,
                "GET",
                f"/repos/{owner}/{repository}/actions/workflows/{quote(workflow_path, safe='')}/runs",
                params={"branch": branch, "event": "workflow_dispatch", "per_page": 20},
            )
        except HTTPException as exc:
            return await self._observe_terminal_failure(run, "inconclusive", exc.detail)
        except Exception:
            return await self._observe_terminal_failure(
                run, "inconclusive", "GitHub candidate verification status is temporarily unavailable."
            )
        runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
        matched = next(
            (
                item
                for item in runs
                if isinstance(item, dict) and str(item.get("head_sha") or "") == candidate_sha
            ),
            None,
        )
        if not matched:
            return await self._update_observed_state(
                run,
                status="pending",
                verification_level="dispatched_not_completed",
                message="Waiting for the configured candidate verification workflow to start.",
            )
        workflow_status = str(matched.get("status") or "pending").lower()
        conclusion = str(matched.get("conclusion") or "").lower()
        safe_run_id = int(matched["id"]) if isinstance(matched.get("id"), int) else None
        safe_url = str(matched.get("html_url")) if isinstance(matched.get("html_url"), str) else None
        if workflow_status != "completed":
            return await self._update_observed_state(
                run,
                status="running" if workflow_status in {"in_progress", "queued", "requested", "waiting"} else "pending",
                verification_level="dispatched_not_completed",
                message="Configured candidate verification workflow is still running.",
                github_run_id=safe_run_id,
                github_url=safe_url,
            )
        if conclusion == "success":
            return await self._update_observed_state(
                run,
                status="succeeded",
                verification_level="targeted_verification_passed",
                message="Configured targeted candidate verification passed. Full CI remains a separate outcome.",
                github_run_id=safe_run_id,
                github_url=safe_url,
            )
        if conclusion in {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}:
            failure_context = await self._collect_failure_evidence(
                user_id=user_id,
                run=run,
                installation_id=installation_id,
                owner=owner,
                repository=repository,
                github_run_id=safe_run_id,
            )
            message = "Configured targeted candidate verification did not pass."
            evidence_count = len(failure_context["evidence_ids"])
            if evidence_count:
                message += (
                    f" Captured {evidence_count} bounded, redacted failure "
                    f"{'excerpt' if evidence_count == 1 else 'excerpts'} for review."
                )
            return await self._update_observed_state(
                run,
                status="failed",
                verification_level="targeted_verification_failed",
                message=message,
                github_run_id=safe_run_id,
                github_url=safe_url,
                failure_evidence_ids=failure_context["evidence_ids"],
                failure_categories=failure_context["categories"],
                logs_truncated=failure_context["truncated"],
                observed_limitations=failure_context["limitations"],
            )
        return await self._update_observed_state(
            run,
            status="inconclusive",
            verification_level="inconclusive",
            message="Candidate verification completed without a recognized conclusion.",
            github_run_id=safe_run_id,
            github_url=safe_url,
        )

    async def get_run(self, *, user_id: str, verification_run_id: str) -> dict[str, Any]:
        """Return a tenant-scoped, browser-safe verification run projection."""

        run = await get_db().verification_runs.find_one({"_id": verification_run_id, "userId": user_id})
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification run not found")
        return self._public_run_state(run)

    async def _collect_failure_evidence(
        self,
        *,
        user_id: str,
        run: dict[str, Any],
        installation_id: str,
        owner: str,
        repository: str,
        github_run_id: int | None,
    ) -> dict[str, Any]:
        """Persist bounded, redacted failed-job evidence for one exact candidate.

        Candidate workflow commands and raw logs are untrusted data. This
        helper never invokes them, never reads successful-job logs, and never
        lets evidence alter approval, delivery, or command policy. A failure
        to fetch metadata or logs is a limitation of the observation, not a
        reason to change its terminal GitHub conclusion.
        """

        result: dict[str, Any] = {
            "evidence_ids": [],
            "categories": [],
            "truncated": False,
            "limitations": [],
        }
        if github_run_id is None:
            result["limitations"].append(
                "GitHub did not provide a workflow-run identifier for failed-job evidence."
            )
            return result
        try:
            session = await get_db().sessions.find_one(
                {"_id": run["sessionId"], "userId": user_id}
            )
        except Exception:
            session = None
        if not session:
            result["limitations"].append(
                "The owned session was unavailable, so candidate failure excerpts were not persisted."
            )
            return result
        try:
            response = await github_installation_api(
                installation_id,
                "GET",
                f"/repos/{owner}/{repository}/actions/runs/{github_run_id}/jobs",
                params={"per_page": get_settings().ci_log_max_jobs_per_run},
            )
        except HTTPException:
            result["limitations"].append(
                "GitHub did not permit access to candidate job metadata."
            )
            return result
        except Exception:
            result["limitations"].append(
                "Candidate job metadata is temporarily unavailable."
            )
            return result

        source_records: list[dict[str, Any]] = []
        categories: set[CIFailureCategory] = set()
        jobs = response.get("jobs", []) if isinstance(response, dict) else []
        for item in jobs[: get_settings().ci_log_max_jobs_per_run]:
            if not isinstance(item, dict) or item.get("conclusion") not in {
                "failure",
                "timed_out",
                "cancelled",
                "startup_failure",
                "action_required",
            }:
                continue
            job_id = item.get("id")
            if not isinstance(job_id, int):
                continue
            failed_step = next(
                (
                    str(step.get("name"))
                    for step in item.get("steps", [])
                    if isinstance(step, dict) and step.get("conclusion") == "failure"
                ),
                None,
            )
            job = WorkflowJobSummary(
                id=job_id,
                run_id=github_run_id,
                name=str(item.get("name") or "candidate job"),
                status=str(item.get("status") or "") or None,
                conclusion=str(item.get("conclusion") or "") or None,
                failed_step=failed_step,
                html_url=str(item.get("html_url") or "") or None,
            )
            try:
                payload, truncated = await github_installation_api_bytes(
                    installation_id,
                    f"/repos/{owner}/{repository}/actions/jobs/{job.id}/logs",
                    max_bytes=get_settings().ci_log_max_download_bytes,
                )
            except HTTPException:
                result["limitations"].append(
                    f"GitHub did not permit access to logs for candidate job {job.name}."
                )
                continue
            except Exception:
                result["limitations"].append(
                    f"Candidate logs for job {job.name} are temporarily unavailable."
                )
                continue
            excerpts = extract_failure_excerpts(
                text=payload.decode("utf-8", errors="replace"),
                run_id=github_run_id,
                job=job,
                truncated=truncated,
            )
            result["truncated"] = bool(result["truncated"] or truncated)
            for excerpt in excerpts:
                categories.update(excerpt.categories)
                source_records.append(
                    {
                        "kind": "ci_log",
                        "source": (
                            f"github-actions/candidate/{run['id']}/"
                            f"run/{github_run_id}/job/{job.id}"
                        ),
                        "excerpt": excerpt.excerpt,
                        "lineStart": excerpt.line_start,
                        "lineEnd": excerpt.line_end,
                        "metadata": {
                            "candidate_verification_id": str(run["id"]),
                            "workflow_run_id": github_run_id,
                            "workflow_path": str(run.get("workflowPath") or ""),
                            "job_id": job.id,
                            "job_name": job.name,
                            "failed_step": job.failed_step,
                            "head_sha": str(run.get("candidateCommitSha") or ""),
                            "branch": str(run.get("candidateBranch") or ""),
                            "conclusion": job.conclusion,
                            "html_url": job.html_url,
                            "categories": [str(category) for category in excerpt.categories],
                            "failed_test_identifiers": excerpt.failed_test_identifiers,
                            "truncated": excerpt.truncated,
                            "untrusted": True,
                            "verification_scope": "targeted_candidate",
                        },
                    }
                )
        if source_records:
            try:
                records = await self.evidence.record_sources(
                    user_id=user_id,
                    session=session,
                    run_id=str(run["id"]),
                    sources=source_records,
                    commit_sha=str(run.get("candidateCommitSha") or "") or None,
                )
                result["evidence_ids"] = [str(record["id"]) for record in records]
            except Exception:
                result["limitations"].append(
                    "Candidate failure excerpts could not be persisted."
                )
        result["categories"] = sorted(str(category) for category in categories)
        result["limitations"] = list(dict.fromkeys(result["limitations"]))[:10]
        return result

    async def get_latest_run(self, *, user_id: str, session_id: str) -> dict[str, Any] | None:
        """Return the most recent verification state for one owned session.

        This is a status projection only. It deliberately does not trigger a
        GitHub request, expose a workflow command, or leak an installation
        identifier to the browser.
        """

        run = await get_db().verification_runs.find_one(
            {"userId": user_id, "sessionId": session_id}, sort=[("createdAt", -1)]
        )
        return self._public_run_state(run) if run else None

    async def _observe_terminal_failure(self, run: dict[str, Any], state: str, detail: str) -> dict[str, Any]:
        return await self._update_observed_state(
            run,
            status=state,
            verification_level="inconclusive",
            message=self.redactor.redact(str(detail))[:500],
        )

    async def _update_observed_state(
        self,
        run: dict[str, Any],
        *,
        status: str,
        verification_level: str,
        message: str,
        github_run_id: int | None = None,
        github_url: str | None = None,
        failure_evidence_ids: list[str] | None = None,
        failure_categories: list[str] | None = None,
        logs_truncated: bool = False,
        observed_limitations: list[str] | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "status": status,
            "verificationLevel": verification_level,
            "message": self.redactor.redact(message)[:500],
            "checkedAt": utc_now(),
            "updatedAt": utc_now(),
        }
        if github_run_id is not None:
            updates["githubRunId"] = github_run_id
        if github_url is not None:
            updates["githubUrl"] = github_url
        if failure_evidence_ids:
            updates["failureEvidenceIds"] = failure_evidence_ids[:50]
        if failure_categories:
            updates["failureCategories"] = failure_categories[:20]
        if logs_truncated:
            updates["logsTruncated"] = True
        if observed_limitations:
            existing_limitations = [
                str(item) for item in run.get("limitations", []) if isinstance(item, str)
            ]
            updates["limitations"] = list(
                dict.fromkeys([*existing_limitations, *observed_limitations])
            )[:20]
        await get_db().verification_runs.update_one(
            {"_id": run["_id"], "userId": run["userId"]}, {"$set": updates}
        )
        return self._public_run_state({**run, **updates})

    @staticmethod
    def _public_run_state(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": str(run.get("status") or "inconclusive"),
            "verificationRunId": str(run.get("id") or run.get("_id")),
            "verificationLevel": str(run.get("verificationLevel") or "not_independently_tested"),
            "message": str(run.get("message") or run.get("failureReason") or ""),
            "githubRunId": run.get("githubRunId") if isinstance(run.get("githubRunId"), int) else None,
            "githubUrl": str(run["githubUrl"]) if run.get("githubUrl") else None,
            "failureEvidenceCount": len(
                [item for item in run.get("failureEvidenceIds", []) if isinstance(item, str)]
            ),
            "failureCategories": [
                str(item) for item in run.get("failureCategories", []) if isinstance(item, str)
            ][:20],
            "logsTruncated": bool(run.get("logsTruncated")),
            "limitations": [
                str(item) for item in run.get("limitations", []) if isinstance(item, str)
            ],
        }

    async def _terminal_failure(self, run: dict[str, Any], state: str, detail: str) -> dict[str, Any]:
        safe_detail = self.redactor.redact(str(detail))[:500]
        await get_db().verification_runs.update_one(
            {"_id": run["_id"], "userId": run["userId"]},
            {
                "$set": {
                    "status": state,
                    "verificationLevel": "not_independently_tested",
                    "failureReason": safe_detail,
                    "updatedAt": utc_now(),
                }
            },
        )
        return {
            "status": state,
            "verificationRunId": run["id"],
            "verificationLevel": "not_independently_tested",
            "reason": safe_detail,
        }
