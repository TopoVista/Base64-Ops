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
from app.memory.memory_service import MemoryService
from app.memory.models import MemoryEntryCandidate, OperationalMemoryKind
from app.schemas.incident import EvidenceStrength, IncidentDiagnosis, IncidentStatus
from app.services.background_job_service import BackgroundJobService
from app.services.ci_investigation_service import CIInvestigationService
from app.services.flaky_test_service import FlakyTestObservationService
from app.services.github_service import list_repositories, normalize_github_repository_url
from app.services.incident_reporting_service import IncidentReportingService
from app.services.prevention_rule_service import PreventionRuleService
from app.services.workspace_service import WorkspaceService
from app.utils.datetime import utc_now
from app.utils.ids import new_id

_DELIVERY_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure", "action_required"})
_GROUP_TIMELINE_MAX_ENTRIES = 25


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
        await db.incidents.create_index(
            [("userId", 1), ("activeGroupKey", 1)],
            unique=True,
            sparse=True,
            name="incident_one_active_group_per_user",
        )
        await FlakyTestObservationService().ensure_indexes()

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
        github_check_reporting_enabled: bool = False,
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
        identity = {"userId": user_id, "sessionId": str(session["_id"])}
        existing = await get_db().repository_automation_settings.find_one(identity, {"_id": 1})
        automation_id = str(existing["_id"]) if existing else new_id("aut_")
        document = {
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
            "githubCheckReportingEnabled": bool(github_check_reporting_enabled),
            "updatedAt": now,
        }
        await get_db().repository_automation_settings.update_one(
            identity,
            {"$set": document, "$setOnInsert": {"_id": automation_id, "createdAt": now}},
            upsert=True,
        )
        document["id"] = automation_id
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
        envelope = self._workflow_run_envelope(payload, event_name=event_name, action=resolved_action)
        if envelope is None:
            return {"accepted": False, "ignored": True, "reason": "Event is not a completed workflow run."}
        targets = await self._matching_automation_targets(envelope)
        now = utc_now()
        conclusion = str(envelope["conclusion"])
        if conclusion == "success":
            event_status = "reconciling"
        elif conclusion in _FAILURE_CONCLUSIONS:
            event_status = "queued" if targets else "ignored"
        else:
            event_status = "ignored"
        event = {
            "_id": new_id("evt_"),
            "deliveryId": delivery_id,
            "eventName": event_name,
            "action": resolved_action or "",
            "repositoryId": envelope["repositoryId"],
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
            "status": event_status,
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
                "status": existing.get("status", event_status),
            }
        if conclusion == "success":
            reconciled = await self._record_same_sha_success(envelope=envelope, targets=targets)
            await get_db().github_webhook_events.update_one(
                {"_id": event["_id"]},
                {
                    "$set": {
                        "status": "reconciled",
                        "reconciledIncidents": reconciled,
                        "updatedAt": utc_now(),
                        "completedAt": utc_now(),
                    }
                },
            )
            return {
                "accepted": True,
                "ignored": True,
                "eventId": event["_id"],
                "reason": "Successful workflow run recorded as durable read-only reconciliation context.",
                "reconciledIncidents": reconciled,
            }
        if conclusion not in _FAILURE_CONCLUSIONS:
            return {
                "accepted": False,
                "ignored": True,
                "eventId": event["_id"],
                "reason": "Workflow conclusion is not investigated.",
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

    def _workflow_run_envelope(
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

    async def _record_same_sha_success(
        self, *, envelope: dict[str, str | int | None], targets: list[dict[str, Any]]
    ) -> int:
        """Attach a read-only same-SHA success signal without closing an incident.

        A later success can indicate a transient condition, but it does not
        establish that the original failure was flaky or that a remediation is
        unnecessary.  The original diagnosis and approval requirements remain
        unchanged.
        """

        now = utc_now()
        reconciled = 0
        workflow_filter: dict[str, Any]
        if envelope.get("workflowPath"):
            workflow_filter = {"workflowPath": envelope["workflowPath"]}
        else:
            workflow_filter = {"workflowName": envelope.get("workflowName")}
        for target in targets:
            result = await get_db().incidents.update_many(
                {
                    "userId": target["userId"],
                    "sessionId": target["sessionId"],
                    "repositoryId": envelope["repositoryId"],
                    "branch": envelope.get("branch"),
                    "headSha": envelope["headSha"],
                    "isGroupRoot": True,
                    "status": IncidentStatus.EVIDENCE_COLLECTED.value,
                    **workflow_filter,
                },
                {
                    "$set": {
                        "sameShaSuccess": {
                            "workflowRunId": envelope["workflowRunId"],
                            "headSha": envelope["headSha"],
                            "observedAt": now,
                        },
                        "updatedAt": now,
                    }
                },
            )
            reconciled += int(result.modified_count)
        return reconciled

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


def build_structured_incident_diagnosis(context: Any) -> IncidentDiagnosis:
    """Return a typed diagnosis whose scope is limited to collected evidence."""

    strength, status, summary = build_incident_diagnosis(context)
    history = getattr(context, "history", None)
    history_context = history.model_dump(mode="json") if history else None
    evidence_ids = [
        *getattr(context, "evidence_ids", []),
        *getattr(context, "history_evidence_ids", []),
        *getattr(context, "workflow_evidence_ids", []),
        *getattr(context, "changed_file_evidence_ids", []),
    ]
    recommended = (
        "Review the existing evidence and create a constrained proposal only if the failure condition "
        "remains applicable."
        if strength is not EvidenceStrength.INSUFFICIENT
        else "Collect additional bounded CI or repository evidence before proposing a change."
    )
    return IncidentDiagnosis(
        status=status,
        summary=summary,
        evidence_strength=strength,
        failure_categories=[str(category) for category in getattr(context, "failure_categories", [])],
        supporting_evidence_ids=list(dict.fromkeys(evidence_ids)),
        relevant_paths=list(getattr(context, "relevant_paths", [])),
        historical_context=history_context,
        applicability=getattr(context, "applicability", None),
        recommended_next_action=recommended,
    )


def incident_memory_candidate(
    *, context: Any, diagnosis: IncidentDiagnosis
) -> MemoryEntryCandidate | None:
    """Build a conservative, evidence-gated historical incident summary.

    This deliberately records the *observed diagnosis*, not a claimed repair
    or merge outcome.  Weak/insufficient investigations must not become
    reusable operational memory.
    """

    if diagnosis.evidence_strength not in {EvidenceStrength.CORRELATED, EvidenceStrength.SUPPORTED}:
        return None
    categories = sorted(set(diagnosis.failure_categories))
    if not categories or not diagnosis.supporting_evidence_ids:
        return None
    workflow_identity = str(
        getattr(context, "workflow_path", None) or getattr(context, "workflow_name", None) or "unknown"
    )
    confidence = 0.75 if diagnosis.evidence_strength is EvidenceStrength.SUPPORTED else 0.65
    return MemoryEntryCandidate(
        kind=OperationalMemoryKind.INCIDENT_OUTCOME,
        key=f"ci-incident:{workflow_identity}:{','.join(categories)}",
        value=diagnosis.summary,
        tags=["ci", "github-actions", *categories],
        confidence=confidence,
        evidence_ids=diagnosis.supporting_evidence_ids,
        affected_paths=diagnosis.relevant_paths[:30],
        metadata={
            "schemaVersion": "incident-memory-v1",
            "applicability": diagnosis.applicability,
            "hypothesisStatus": diagnosis.status,
            "historical": bool(getattr(context, "run_is_historical", False)),
        },
    )
def incident_group_key(*, event: dict[str, Any], context: Any) -> str:
    """Return a conservative stable key for repeated evidence-equivalent failures."""

    failed_jobs = [
        {"name": job.name, "failed_step": job.failed_step or ""}
        for job in getattr(context, "failed_jobs", [])
    ]
    signature = {
        "repository_id": str(event["repositoryId"]),
        "workflow": str(event.get("workflowPath") or event.get("workflowName") or ""),
        "branch": str(event.get("branch") or ""),
        "head_sha": str(event["headSha"]),
        "failure_categories": sorted(str(category) for category in getattr(context, "failure_categories", [])),
        "failed_jobs": sorted(failed_jobs, key=lambda job: (job["name"], job["failed_step"])),
    }
    encoded = json.dumps(signature, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "igr_" + hashlib.sha256(encoded).hexdigest()[:40]


def _flaky_signal(context: Any) -> str | None:
    """Return a conservative label only after mixed same-SHA execution history."""

    history = getattr(context, "history", None)
    if (
        getattr(history, "assessment", None) == "mixed_outcomes_same_sha"
        and getattr(context, "failed_test_identifiers", [])
    ):
        return "suspected"
    return None


def incident_group_timeline_entry(*, event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Summarize one grouped delivery without retaining raw provider output."""

    return {
        "workflowRunId": int(event["workflowRunId"]),
        "headSha": str(event["headSha"]),
        "observedAt": utc_now(),
        "failureCategories": sorted(str(category) for category in getattr(context, "failure_categories", [])),
        "failedJobNames": sorted({str(job.name) for job in getattr(context, "failed_jobs", []) if job.name}),
    }


def incident_dispatch_is_complete(incident: dict[str, Any]) -> bool:
    """Return whether a duplicate event has reached an immutable terminal state.

    Evidence collection is not terminal until the record has either become a
    group root or been attached to an existing group.  Retrying an interrupted
    grouping step is safe and prevents a delivered webhook from disappearing
    from the Inbox merely because the final grouping write briefly failed.
    """

    status_value = incident.get("status")
    if status_value in {IncidentStatus.GROUPED.value, IncidentStatus.IGNORED.value}:
        return True
    return status_value == IncidentStatus.EVIDENCE_COLLECTED.value and bool(incident.get("groupKey"))


class IncidentDispatchService:
    """Run bounded, read-only CI evidence collection outside webhook requests."""

    def __init__(
        self,
        *,
        ci_investigation: CIInvestigationService | None = None,
        workspace: WorkspaceService | None = None,
        memory: MemoryService | None = None,
        flaky_observations: FlakyTestObservationService | None = None,
        prevention_rules: PreventionRuleService | None = None,
    ) -> None:
        self.workspace = workspace or WorkspaceService()
        self.ci_investigation = ci_investigation or CIInvestigationService(workspace=self.workspace)
        self.memory = memory or MemoryService()
        self.flaky_observations = flaky_observations or FlakyTestObservationService()
        self.prevention_rules = prevention_rules or PreventionRuleService()

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
            "failedTestIdentifiers": [],
            "flakySignal": None,
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
            if not existing:
                raise
            if incident_dispatch_is_complete(existing):
                return
            incident_id = str(existing["_id"])

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
        diagnosis = build_structured_incident_diagnosis(context)
        flaky_observations = await self._record_flaky_observations(
            incident_id=incident_id,
            event=event,
            session=session,
            context=context,
        )
        await db.incidents.update_one(
            {"_id": incident_id, "userId": automation["userId"]},
            {
                "$set": {
                    "status": IncidentStatus.EVIDENCE_COLLECTED.value,
                    "failureCategories": [str(category) for category in context.failure_categories],
                    "failedTestIdentifiers": context.failed_test_identifiers,
                    "flakySignal": _flaky_signal(context),
                    "flakyObservations": flaky_observations,
                    "evidenceStrength": diagnosis.evidence_strength.value,
                    "hypothesisStatus": diagnosis.status,
                    "diagnosisSummary": diagnosis.summary,
                    "diagnosis": diagnosis.model_dump(mode="json"),
                    "evidenceIds": context.evidence_ids,
                    "historyEvidenceIds": context.history_evidence_ids,
                    "workflowEvidenceIds": context.workflow_evidence_ids,
                    "changedFileEvidenceIds": context.changed_file_evidence_ids,
                    "relevantPaths": context.relevant_paths,
                    "currentHeadSha": context.current_head_sha,
                    "runIsHistorical": context.run_is_historical,
                    "applicability": context.applicability,
                    "applicabilityReason": context.applicability_reason,
                    "history": context.history.model_dump(mode="json") if context.history else None,
                    "incomplete": context.incomplete,
                    "limitations": context.limitations,
                    "updatedAt": utc_now(),
                }
            },
        )
        await self._record_incident_memory(
            incident_id=incident_id,
            event=event,
            session=session,
            context=context,
            diagnosis=diagnosis,
        )
        await self._assign_incident_group(
            incident_id=incident_id,
            event=event,
            automation=automation,
            context=context,
        )
        await self._refresh_prevention_rules(
            incident_id=incident_id,
            automation=automation,
            repository_id=str(event["repositoryId"]),
        )
        await self._publish_github_check(
            incident={
                **incident,
                "status": IncidentStatus.EVIDENCE_COLLECTED.value,
                "failureCategories": [str(category) for category in context.failure_categories],
                "evidenceStrength": diagnosis.evidence_strength.value,
                "diagnosisSummary": diagnosis.summary,
                "diagnosis": diagnosis.model_dump(mode="json"),
            },
            automation=automation,
        )

    async def _refresh_prevention_rules(
        self,
        *,
        incident_id: str,
        automation: dict[str, Any],
        repository_id: str,
    ) -> None:
        """Persist only eligible recurrence patterns after incident grouping.

        A persistence failure must not hide durable incident evidence or make
        the worker retry GitHub collection. It becomes an explicit limitation.
        """

        try:
            await self.prevention_rules.refresh_for_repository(
                user_id=str(automation["userId"]),
                session_id=str(automation["sessionId"]),
                repository_id=repository_id,
            )
        except Exception:
            await get_db().incidents.update_one(
                {"_id": incident_id, "userId": automation["userId"]},
                {
                    "$addToSet": {"limitations": "Historical prevention rules could not be refreshed."},
                    "$set": {"updatedAt": utc_now()},
                },
            )

    async def _publish_github_check(self, *, incident: dict[str, Any], automation: dict[str, Any]) -> None:
        """Best-effort native reporting after durable evidence/grouping writes.

        Reporting cannot determine whether an incident exists or whether it is
        grouped. A permissions or provider failure becomes a bounded
        limitation, never a worker retry that repeats investigation work.
        """

        if not automation.get("githubCheckReportingEnabled"):
            return
        db = get_db()
        candidate_settings = await db.candidate_verification_settings.find_one(
            {
                "userId": automation["userId"],
                "sessionId": automation["sessionId"],
                "repositoryId": incident["repositoryId"],
                "enabled": True,
            }
        )
        result = await IncidentReportingService().publish_check(
            incident=incident,
            automation=automation,
            candidate_settings=candidate_settings,
        )
        if result.get("status") == "published":
            await db.incidents.update_one(
                {"_id": incident["_id"], "userId": automation["userId"]},
                {"$set": {"githubCheckReport": result, "updatedAt": utc_now()}},
            )
            return
        message = str(result.get("message") or "GitHub Check reporting was unavailable.")[:300]
        await db.incidents.update_one(
            {"_id": incident["_id"], "userId": automation["userId"]},
            {
                "$set": {
                    "githubCheckReport": {"status": str(result.get("status") or "unavailable"), "message": message},
                    "updatedAt": utc_now(),
                },
                "$addToSet": {"limitations": message},
            },
        )

    async def _record_flaky_observations(
        self,
        *,
        incident_id: str,
        event: dict[str, Any],
        session: dict[str, Any],
        context: Any,
    ) -> list[dict[str, Any]]:
        """Record bounded observations without making flaky CI a hard dependency.

        Failure histories are useful supplemental evidence.  A temporary write
        failure must not hide the underlying incident or turn an investigation
        into a retry loop.
        """

        try:
            return await self.flaky_observations.record(
                user_id=str(session["userId"]),
                session_id=str(session["_id"]),
                repository_id=str(event["repositoryId"]),
                incident_id=incident_id,
                context=context,
            )
        except Exception:
            limitation = "Flaky-test observations could not be recorded."
            if limitation not in context.limitations:
                context.limitations.append(limitation)
            return []

    async def _record_incident_memory(
        self,
        *,
        incident_id: str,
        event: dict[str, Any],
        session: dict[str, Any],
        context: Any,
        diagnosis: IncidentDiagnosis,
    ) -> None:
        candidate = incident_memory_candidate(context=context, diagnosis=diagnosis)
        if candidate is None:
            return
        evidence_ids = set(diagnosis.supporting_evidence_ids)
        try:
            entries = await self.memory.record_incident_outcome(
                user_id=str(session["userId"]),
                repository_id=str(session["repoUrl"]),
                branch=str(event.get("branch") or session.get("defaultBranch") or "main"),
                commit_sha=str(event["headSha"]),
                run_id=incident_id,
                verified_evidence_ids=evidence_ids,
                candidates=[candidate],
            )
        except Exception:
            # Memory improves later investigations but cannot make a durable,
            # evidence-backed incident disappear. Keep the dispatch retry-free
            # here and leave an observable limitation for an operator.
            await get_db().incidents.update_one(
                {"_id": incident_id, "userId": session["userId"]},
                {
                    "$addToSet": {"limitations": "Historical incident memory could not be recorded."},
                    "$set": {"updatedAt": utc_now()},
                },
            )
            return
        if entries:
            await get_db().incidents.update_one(
                {"_id": incident_id, "userId": session["userId"]},
                {
                    "$set": {
                        "memoryEntryCount": len(entries),
                        "memoryRecordedAt": utc_now(),
                        "updatedAt": utc_now(),
                    }
                },
            )

    async def _assign_incident_group(
        self,
        *,
        incident_id: str,
        event: dict[str, Any],
        automation: dict[str, Any],
        context: Any,
    ) -> None:
        """Group only an evidence-equivalent failure while keeping every event traceable."""

        db = get_db()
        key = incident_group_key(event=event, context=context)
        timeline_entry = incident_group_timeline_entry(event=event, context=context)
        existing = await db.incidents.find_one(
            {
                "userId": automation["userId"],
                "activeGroupKey": key,
                "isGroupRoot": True,
                "_id": {"$ne": incident_id},
            }
        )
        if existing:
            await self._attach_to_existing_group(
                root_id=str(existing["_id"]),
                incident_id=incident_id,
                event=event,
                automation=automation,
                key=key,
                timeline_entry=timeline_entry,
            )
            return
        try:
            await db.incidents.update_one(
                {"_id": incident_id, "userId": automation["userId"]},
                {
                    "$set": {
                        "groupKey": key,
                        "activeGroupKey": key,
                        "isGroupRoot": True,
                        "groupRootId": incident_id,
                        "groupRunIds": [event["workflowRunId"]],
                        "groupTimeline": [timeline_entry],
                        "firstObservedAt": timeline_entry["observedAt"],
                        "lastObservedAt": timeline_entry["observedAt"],
                        "updatedAt": utc_now(),
                    }
                },
            )
        except DuplicateKeyError:
            winner = await db.incidents.find_one(
                {"userId": automation["userId"], "activeGroupKey": key, "isGroupRoot": True}
            )
            if not winner:
                raise
            await self._attach_to_existing_group(
                root_id=str(winner["_id"]),
                incident_id=incident_id,
                event=event,
                automation=automation,
                key=key,
                timeline_entry=timeline_entry,
            )

    async def _attach_to_existing_group(
        self,
        *,
        root_id: str,
        incident_id: str,
        event: dict[str, Any],
        automation: dict[str, Any],
        key: str,
        timeline_entry: dict[str, Any],
    ) -> None:
        db = get_db()
        now = utc_now()
        await db.incidents.update_one(
            {"_id": root_id, "userId": automation["userId"], "isGroupRoot": True},
            {
                "$addToSet": {
                    "groupRunIds": event["workflowRunId"],
                    "groupMemberIncidentIds": incident_id,
                },
                "$push": {"groupTimeline": {"$each": [timeline_entry], "$slice": -_GROUP_TIMELINE_MAX_ENTRIES}},
                "$set": {"lastObservedAt": timeline_entry["observedAt"], "updatedAt": now},
            },
        )
        await db.incidents.update_one(
            {"_id": incident_id, "userId": automation["userId"]},
            {
                "$set": {
                    "status": IncidentStatus.GROUPED.value,
                    "groupKey": key,
                    "groupRootId": root_id,
                    "isGroupRoot": False,
                    "updatedAt": now,
                }
            },
        )
