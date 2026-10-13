"""Durable, tenant-scoped intake for opt-in GitHub Actions incidents.

The webhook boundary accepts only a small authenticated envelope. It never
clones a repository, calls a model, executes a command, or grants any mutation
permission. Those operations remain in existing, separately authorized flows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.db.mongo import get_db
from app.schemas.incident import EvidenceStrength, IncidentStatus
from app.services.background_job_service import BackgroundJobService
from app.services.ci_investigation_service import CIInvestigationService
from app.services.github_service import list_repositories, normalize_github_repository_url
from app.services.workspace_service import WorkspaceService
from app.utils.datetime import utc_now
from app.utils.ids import new_id

_DELIVERY_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})


class GitHubWebhookService:
    """Accept signed GitHub events and dispatch only opt-in repository work."""

    async def ensure_indexes(self) -> None:
        db = get_db()
        await db.github_webhook_events.create_index("deliveryId", unique=True, name="github_delivery_once")
        await db.github_webhook_events.create_index([("repositoryId", 1), ("status", 1), ("createdAt", -1)])
        await db.github_webhook_events.create_index(
            "expiresAt", expireAfterSeconds=0, name="github_webhook_event_expiry"
        )
        await db.repository_automation_settings.create_index(
            [("userId", 1), ("sessionId", 1)], unique=True, name="automation_one_session"
        )
        await db.repository_automation_settings.create_index(
            [("repositoryId", 1), ("enabled", 1)], name="automation_repository_lookup"
        )
        await db.incidents.create_index(
            [("eventId", 1), ("automationId", 1)], unique=True, name="incident_one_per_event_target"
        )
        await db.incidents.create_index([("userId", 1), ("updatedAt", -1)], name="incident_user_timeline")

    @staticmethod
    def verify_signature(*, body: bytes, signature: str, secret: str) -> bool:
        if not signature.startswith("sha256=") or not secret:
            return False
        received = signature.removeprefix("sha256=")
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, received)

    async def configure_repository(
        self,
        *,
        user_id: str,
        session: dict[str, Any],
        enabled: bool,
        workflow_names: list[str],
        include_branches: list[str],
        exclude_branches: list[str],
        daily_investigation_limit: int,
    ) -> dict[str, Any]:
        """Derive the GitHub numeric repository ID from the user's own OAuth view."""

        repo_url = str(session.get("repoUrl", ""))
        _owner, _name, canonical_url = normalize_github_repository_url(repo_url)
        repos = await list_repositories(user_id)
        repository = next((item for item in repos if str(item.get("cloneUrl")) == canonical_url), None)
        if not repository or not repository.get("id"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository is unavailable to this GitHub account.",
            )
        for branch in [*include_branches, *exclude_branches]:
            if not _BRANCH.fullmatch(branch):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid branch filter.")
        now = utc_now()
        document = {
            "_id": new_id("aut_"),
            "userId": user_id,
            "sessionId": str(session["_id"]),
            "repositoryId": str(repository["id"]),
            "repositoryName": str(repository.get("fullName") or session.get("repoName") or "repository"),
            "repositoryUrl": canonical_url,
            "enabled": enabled,
            "workflowNames": sorted({name.strip() for name in workflow_names if name.strip()})[:30],
            "includeBranches": sorted({name.strip() for name in include_branches if name.strip()})[:30],
            "excludeBranches": sorted({name.strip() for name in exclude_branches if name.strip()})[:30],
            "dailyInvestigationLimit": daily_investigation_limit,
            "updatedAt": now,
        }
        await get_db().repository_automation_settings.update_one(
            {"userId": user_id, "sessionId": str(session["_id"])},
            {"$set": document, "$setOnInsert": {"createdAt": now}},
            upsert=True,
        )
        document["id"] = document.pop("_id")
        return document

    async def get_repository_settings(self, *, user_id: str, session_id: str) -> dict[str, Any] | None:
        return await get_db().repository_automation_settings.find_one({"userId": user_id, "sessionId": session_id})

    async def accept_delivery(
        self,
        *,
        body: bytes,
        event_name: str,
        action: str | None,
        delivery_id: str,
        signature: str,
    ) -> dict[str, Any]:
        settings = get_settings()
        if not settings.github_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub webhook intake is not configured.",
            )
        if len(body) > settings.github_webhook_max_body_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload is too large.",
            )
        if not _DELIVERY_ID.fullmatch(delivery_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub delivery identifier.")
        if not self.verify_signature(body=body, signature=signature, secret=settings.github_webhook_secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid GitHub webhook signature.")
        try:
            payload = json.loads(body)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub webhook payload."
            ) from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub webhook payload.")

        payload_action = payload.get("action")
        resolved_action = action or (payload_action if isinstance(payload_action, str) else None)
        envelope = self._workflow_failure_envelope(payload, event_name=event_name, action=resolved_action)
        if envelope is None:
            return {"accepted": False, "ignored": True, "reason": "Event is not a completed failed workflow run."}
        repository_id = envelope["repositoryId"]
        targets = await self._matching_automation_targets(envelope)
        now = utc_now()
        event = {
            "_id": new_id("evt_"),
            "deliveryId": delivery_id,
            "eventName": event_name,
            "action": resolved_action or "",
            "repositoryId": repository_id,
            "repositoryName": envelope["repositoryName"],
            "repositoryUrl": envelope["repositoryUrl"],
            "workflowRunId": envelope["workflowRunId"],
            "workflowName": envelope["workflowName"],
            "workflowPath": envelope["workflowPath"],
            "branch": envelope["branch"],
            "headSha": envelope["headSha"],
            "conclusion": envelope["conclusion"],
            "payloadHash": hashlib.sha256(body).hexdigest(),
            "targetAutomationIds": [target["_id"] for target in targets],
            "status": "queued" if targets else "ignored",
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": now + timedelta(days=settings.github_incident_event_retention_days),
        }
        try:
            await get_db().github_webhook_events.insert_one(event)
        except DuplicateKeyError:
            existing = await get_db().github_webhook_events.find_one({"deliveryId": delivery_id})
            if not existing:
                raise
            return {
                "accepted": True,
                "duplicate": True,
                "eventId": existing["_id"],
                "status": existing.get("status", "queued"),
            }
        if not targets:
            return {"accepted": True, "eventId": event["_id"], "status": "ignored", "targets": 0}
        try:
            queued = await BackgroundJobService().enqueue_incident_dispatch(event_id=event["_id"])
        except Exception:
            await get_db().github_webhook_events.update_one(
                {"_id": event["_id"]}, {"$set": {"status": "dispatch_pending", "updatedAt": utc_now()}}
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook dispatch is temporarily unavailable.",
            ) from None
        await get_db().github_webhook_events.update_one(
            {"_id": event["_id"]},
            {"$set": {"dispatchJobId": queued["id"], "status": "queued", "updatedAt": utc_now()}},
        )
        return {"accepted": True, "eventId": event["_id"], "status": "queued", "targets": len(targets)}

    def _workflow_failure_envelope(
        self, payload: dict[str, Any], *, event_name: str, action: str | None
    ) -> dict[str, str | int | None] | None:
        if event_name != "workflow_run" or action != "completed":
            return None
        repository = payload.get("repository")
        workflow_run = payload.get("workflow_run")
        if not isinstance(repository, dict) or not isinstance(workflow_run, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing workflow-run repository metadata.",
            )
        conclusion = str(workflow_run.get("conclusion") or "").lower()
        if conclusion not in _FAILURE_CONCLUSIONS:
            return None
        repository_id = repository.get("id")
        run_id = workflow_run.get("id")
        clone_url = repository.get("clone_url")
        head_sha = workflow_run.get("head_sha")
        if (
            not repository_id
            or not run_id
            or not isinstance(clone_url, str)
            or not isinstance(head_sha, str)
            or not head_sha
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incomplete workflow-run metadata.")
        _owner, _repository, canonical_url = normalize_github_repository_url(clone_url)
        return {
            "repositoryId": str(repository_id),
            "repositoryName": str(repository.get("full_name") or _repository),
            "repositoryUrl": canonical_url,
            "workflowRunId": int(run_id),
            "workflowName": str(workflow_run.get("name") or "") or None,
            "workflowPath": str(workflow_run.get("path") or "") or None,
            "branch": str(workflow_run.get("head_branch") or "") or None,
            "headSha": head_sha,
            "conclusion": conclusion,
        }

    async def _matching_automation_targets(self, envelope: dict[str, str | int | None]) -> list[dict[str, Any]]:
        cursor = get_db().repository_automation_settings.find(
            {"repositoryId": envelope["repositoryId"], "enabled": True}
        )
        matched: list[dict[str, Any]] = []
        async for target in cursor:
            workflows = set(target.get("workflowNames") or [])
            branch = str(envelope.get("branch") or "")
            if workflows and str(envelope.get("workflowName") or "") not in workflows:
                continue
            if target.get("includeBranches") and branch not in set(target["includeBranches"]):
                continue
            if branch in set(target.get("excludeBranches") or []):
                continue
            matched.append(target)
        return matched


def build_incident_diagnosis(context: Any) -> tuple[EvidenceStrength, str, str]:
    """Produce a conservative structured result from persisted CI evidence.

    This is intentionally not an LLM verdict. A changed file is reported as
    correlation, not proof; missing evidence remains explicit.
    """

    evidence_count = (
        len(context.evidence_ids)
        + len(context.workflow_evidence_ids)
        + len(context.changed_file_evidence_ids)
    )
    if not context.evidence_ids:
        return (
            EvidenceStrength.INSUFFICIENT,
            "insufficient",
            "No failed-job log excerpt was available for a root-cause assessment.",
        )
    if context.incomplete:
        return (
            EvidenceStrength.OBSERVED,
            "candidate",
            "Failure evidence was collected, but one or more required comparisons were unavailable.",
        )
    if context.workflow_evidence_ids and context.changed_file_evidence_ids:
        return EvidenceStrength.SUPPORTED, "supported", (
            "Actions failure evidence, workflow state, and "
            f"{len(context.changed_file_evidence_ids)} correlated repository file(s) were collected. "
            "The correlation is not proof of causation."
        )
    if evidence_count:
        return (
            EvidenceStrength.CORRELATED,
            "candidate",
            "Actions failure evidence was collected with bounded repository correlation.",
        )
    return EvidenceStrength.INSUFFICIENT, "insufficient", "Evidence collection did not produce a diagnosis basis."


class IncidentDispatchService:
    """Run bounded, read-only CI evidence collection outside webhook requests."""

    def __init__(
        self,
        *,
        ci_investigation: CIInvestigationService | None = None,
        workspace: WorkspaceService | None = None,
    ) -> None:
        self.workspace = workspace or WorkspaceService()
        self.ci_investigation = ci_investigation or CIInvestigationService(workspace=self.workspace)

    async def dispatch(self, *, event_id: str) -> None:
        db = get_db()
        event = await db.github_webhook_events.find_one({"_id": event_id})
        if not event or event.get("status") in {"completed", "ignored"}:
            return
        await db.github_webhook_events.update_one(
            {"_id": event_id}, {"$set": {"status": "processing", "updatedAt": utc_now()}}
        )
        completed_targets = 0
        async for automation in db.repository_automation_settings.find(
            {"_id": {"$in": list(event.get("targetAutomationIds") or [])}, "enabled": True}
        ):
            await self._dispatch_target(event=event, automation=automation)
            completed_targets += 1
        await db.github_webhook_events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "status": "completed" if completed_targets else "ignored",
                    "processedTargets": completed_targets,
                    "updatedAt": utc_now(),
                    "completedAt": utc_now(),
                }
            },
        )

    async def mark_dispatch_failure(self, *, event_id: str, error: str, terminal: bool) -> None:
        """Record bounded retry/terminal state without leaking provider details."""

        await get_db().github_webhook_events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "status": "failed" if terminal else "retrying",
                    "lastError": error[:300],
                    "updatedAt": utc_now(),
                    **({"completedAt": utc_now()} if terminal else {}),
                }
            },
        )

    async def _dispatch_target(self, *, event: dict[str, Any], automation: dict[str, Any]) -> None:
        db = get_db()
        now = utc_now()
        incident_id = new_id("inc_")
        incident = {
            "_id": incident_id,
            "eventId": event["_id"],
            "automationId": automation["_id"],
            "userId": automation["userId"],
            "sessionId": automation["sessionId"],
            "repositoryId": event["repositoryId"],
            "repositoryName": event["repositoryName"],
            "repositoryUrl": event["repositoryUrl"],
            "workflowRunId": event["workflowRunId"],
            "workflowName": event.get("workflowName"),
            "workflowPath": event.get("workflowPath"),
            "branch": event.get("branch"),
            "headSha": event["headSha"],
            "status": IncidentStatus.INVESTIGATING.value,
            "failureCategories": [],
            "evidenceStrength": EvidenceStrength.INSUFFICIENT.value,
            "evidenceIds": [],
            "workflowEvidenceIds": [],
            "changedFileEvidenceIds": [],
            "relevantPaths": [],
            "limitations": [],
            "incomplete": False,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await db.incidents.insert_one(incident)
        except DuplicateKeyError:
            existing = await db.incidents.find_one({"eventId": event["_id"], "automationId": automation["_id"]})
            if existing:
                return
            raise

        # Budget is checked inside the worker against durable records, not a
        # browser counter. The just-created record counts as one event; a
        # disabled/over-budget repository receives an observable ignored
        # outcome instead of silently spending another investigation.
        previous_count = await db.incidents.count_documents(
            {
                "automationId": automation["_id"],
                "createdAt": {"$gte": now - timedelta(days=1)},
                "_id": {"$ne": incident_id},
                "status": {"$nin": [IncidentStatus.IGNORED.value, IncidentStatus.FAILED.value]},
            }
        )
        if previous_count >= int(automation.get("dailyInvestigationLimit", 5)):
            await db.incidents.update_one(
                {"_id": incident_id, "userId": automation["userId"]},
                {
                    "$set": {
                        "status": IncidentStatus.IGNORED.value,
                        "incomplete": True,
                        "limitations": ["Repository daily investigation budget was exhausted."],
                        "updatedAt": utc_now(),
                    }
                },
            )
            return

        session = await db.sessions.find_one({"_id": automation["sessionId"], "userId": automation["userId"]})
        if not session:
            await db.incidents.update_one(
                {"_id": incident_id, "userId": automation["userId"]},
                {
                    "$set": {
                        "status": IncidentStatus.NEEDS_ATTENTION.value,
                        "incomplete": True,
                        "limitations": ["The selected repository session is no longer available."],
                        "updatedAt": utc_now(),
                    }
                },
            )
            return

        # The clone is a normal read-only workspace operation executed by the
        # worker. The webhook payload never supplies this path or a command.
        repo_path, _ = await self.workspace.ensure_workspace(
            str(automation["userId"]),
            str(session["slugId"]),
            str(session["repoUrl"]),
            str(session.get("defaultBranch") or "main"),
        )
        current_head_sha = self.workspace.head_commit(repo_path)
        context = await self.ci_investigation.investigate_actions_run(
            user_id=str(automation["userId"]),
            repository_id=str(session["repoUrl"]),
            run_id=int(event["workflowRunId"]),
            session=session,
            graph_run_id=incident_id,
            current_head_sha=current_head_sha,
            repo_path=repo_path,
        )
        strength, hypothesis_status, summary = build_incident_diagnosis(context)
        await db.incidents.update_one(
            {"_id": incident_id, "userId": automation["userId"]},
            {
                "$set": {
                    "status": IncidentStatus.EVIDENCE_COLLECTED.value,
                    "failureCategories": [str(category) for category in context.failure_categories],
                    "evidenceStrength": strength.value,
                    "hypothesisStatus": hypothesis_status,
                    "diagnosisSummary": summary,
                    "evidenceIds": context.evidence_ids,
                    "workflowEvidenceIds": context.workflow_evidence_ids,
                    "changedFileEvidenceIds": context.changed_file_evidence_ids,
                    "relevantPaths": context.relevant_paths,
                    "currentHeadSha": context.current_head_sha,
                    "runIsHistorical": context.run_is_historical,
                    "applicability": context.applicability,
                    "applicabilityReason": context.applicability_reason,
                    "incomplete": context.incomplete,
                    "limitations": context.limitations,
                    "updatedAt": utc_now(),
                }
            },
        )
