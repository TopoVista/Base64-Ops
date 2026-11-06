"""Tenant-scoped incident automation configuration and read APIs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.deps import get_current_user
from app.db.mongo import get_db
from app.memory.memory_service import MemoryService
from app.memory.models import MemoryEntryCandidate, OperationalMemoryKind
from app.schemas.incident import (
    CandidateVerificationSettingsRequest,
    CandidateVerificationSettingsResponse,
    FlakyTestObservationSummary,
    IncidentFeedbackRequest,
    RepositoryAutomationSettingsRequest,
    RepositoryAutomationSettingsResponse,
)
from app.schemas.regression_prevention import PreventionRuleSummary
from app.services.candidate_verification_service import CandidateVerificationService
from app.services.github_service import github_app_is_configured
from app.services.incident_export_service import (
    build_incident_export,
    incident_evidence_ids,
    incident_export_markdown,
)
from app.services.incident_service import GitHubWebhookService
from app.services.organization_incident_graph_service import OrganizationIncidentGraphService
from app.services.prevention_rule_service import PreventionRuleService
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now

router = APIRouter()


def _settings_response(document: dict) -> RepositoryAutomationSettingsResponse:
    return RepositoryAutomationSettingsResponse(
        id=str(document.get("id") or document.get("_id")),
        session_id=str(document["sessionId"]),
        repository_id=str(document["repositoryId"]),
        enabled=bool(document["enabled"]),
        workflow_names=list(document.get("workflowNames") or []),
        include_branches=list(document.get("includeBranches") or []),
        exclude_branches=list(document.get("excludeBranches") or []),
        daily_investigation_limit=int(document["dailyInvestigationLimit"]),
        github_check_reporting_enabled=bool(document.get("githubCheckReportingEnabled")),
        updated_at=document["updatedAt"],
    )


def _candidate_verification_settings_response(document: dict) -> CandidateVerificationSettingsResponse:
    return CandidateVerificationSettingsResponse(
        id=str(document.get("id") or document.get("_id")),
        session_id=str(document["sessionId"]),
        repository_id=str(document.get("repositoryId") or ""),
        repository_full_name=(
            str(document["repositoryFullName"]) if document.get("repositoryFullName") else None
        ),
        enabled=bool(document.get("enabled")),
        workflow_path=str(document["workflowPath"]) if document.get("workflowPath") else None,
        configured=github_app_is_configured(),
        updated_at=document["updatedAt"],
    )


def _incident_response(document: dict) -> dict:
    """Return only user-safe incident fields; internal routing stays private."""

    allowed = {
        "_id",
        "repositoryId",
        "repositoryName",
        "workflowRunId",
        "workflowName",
        "workflowPath",
        "branch",
        "headSha",
        "currentHeadSha",
        "runIsHistorical",
        "status",
        "failureCategories",
        "failedTestIdentifiers",
        "flakySignal",
        "flakyObservations",
        "evidenceStrength",
        "hypothesisStatus",
        "diagnosisSummary",
        "diagnosis",
        "evidenceIds",
        "historyEvidenceIds",
        "workflowEvidenceIds",
        "changedFileEvidenceIds",
        "relevantPaths",
        "applicability",
        "applicabilityReason",
        "history",
        "incomplete",
        "limitations",
        "groupKey",
        "groupRootId",
        "groupRunIds",
        "groupTimeline",
        "firstObservedAt",
        "lastObservedAt",
        "sameShaSuccess",
        "memoryEntryCount",
        "memoryRecordedAt",
        "githubCheckReport",
        "feedback",
        "createdAt",
        "updatedAt",
    }
    response = {key: value for key, value in document.items() if key in allowed}
    response["id"] = str(response.pop("_id"))
    return response


async def _record_incorrect_feedback_memory(*, user_id: str, incident_id: str, incident: dict, correction: str) -> None:
    """Persist an explicit correction through the existing memory lifecycle.

    The browser never supplies the repository identity used by memory.  It is
    resolved from the owned session that produced the incident, and the saved
    value has already gone through the central redactor.
    """

    session_id = incident.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return
    session = await get_db().sessions.find_one(
        {"_id": session_id, "userId": user_id},
        {"repoUrl": 1, "defaultBranch": 1},
    )
    if not isinstance(session, dict):
        return
    repository_id = session.get("repoUrl")
    if not isinstance(repository_id, str) or not repository_id:
        return

    categories = [item for item in incident.get("failureCategories", []) if isinstance(item, str)][:8]
    paths = [item for item in incident.get("relevantPaths", []) if isinstance(item, str)][:30]
    workflow_run_id = incident.get("workflowRunId")
    run_label = str(workflow_run_id) if isinstance(workflow_run_id, int | str) else incident_id
    await MemoryService().record_user_correction(
        user_id=user_id,
        repository_id=repository_id,
        branch=str(incident.get("branch") or session.get("defaultBranch") or "unknown"),
        commit_sha=str(incident.get("headSha") or "unknown"),
        run_id=f"incident:{incident_id}",
        candidates=[
            MemoryEntryCandidate(
                kind=OperationalMemoryKind.USER_CORRECTION,
                key=f"ci-incident-correction:{incident_id}",
                value=f"User marked CI incident run {run_label} incorrect: {correction}",
                tags=["ci", "github-actions", "user-correction", *categories],
                confidence=1.0,
                affected_paths=paths,
                metadata={
                    "schemaVersion": "incident-feedback-memory-v1",
                    "incidentId": incident_id,
                    "workflowRunId": workflow_run_id,
                    "untrusted": True,
                },
            )
        ],
    )


def _flaky_observation_response(document: dict) -> FlakyTestObservationSummary:
    """Translate the storage document without exposing tenant or evidence internals."""

    return FlakyTestObservationSummary(
        id=str(document.get("_id")),
        repository_id=str(document["repositoryId"]),
        workflow_key=str(document["workflowKey"]),
        test_identifier=str(document["testIdentifier"]),
        head_sha=str(document["headSha"]),
        assessment=str(document["assessment"]),
        same_sha_failure_count=int(document.get("sameShaFailureCount", 0)),
        same_sha_success_count=int(document.get("sameShaSuccessCount", 0)),
        limitations=list(document.get("limitations") or []),
        updated_at=document["updatedAt"],
    )


def _prevention_rule_response(document: dict) -> PreventionRuleSummary:
    """Project one persisted rule into browser-safe, advisory-only fields."""

    status_value = str(document.get("status") or "needs_review")
    status = status_value if status_value in {"active", "needs_review"} else "needs_review"
    return PreventionRuleSummary(
        id=str(document.get("_id")),
        repository_id=str(document.get("repositoryId") or ""),
        affected_path=str(document.get("affectedPath") or ""),
        failure_category=str(document.get("failureCategory") or "unknown"),
        historical_incident_ids=[
            value for value in document.get("historicalIncidentIds", []) if isinstance(value, str)
        ][:16],
        historical_run_shas=[
            value for value in document.get("historicalRunShas", []) if isinstance(value, str)
        ][:16],
        occurrence_count=max(2, int(document.get("occurrenceCount") or 2)),
        status=status,
        applicability_limitations=[
            value for value in document.get("applicabilityLimitations", []) if isinstance(value, str)
        ][:8],
        invalidation_reason=(
            str(document["invalidationReason"]) if isinstance(document.get("invalidationReason"), str) else None
        ),
        created_at=document["createdAt"],
        updated_at=document["updatedAt"],
    )


@router.put(
    "/sessions/{slug_id}/automation",
    response_model=RepositoryAutomationSettingsResponse,
    response_model_exclude_none=True,
)
async def configure_automation(
    slug_id: str,
    payload: RepositoryAutomationSettingsRequest,
    user: dict = Depends(get_current_user),
) -> RepositoryAutomationSettingsResponse:
    session = await get_db().sessions.find_one({"userId": user["_id"], "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    result = await GitHubWebhookService().configure_repository(
        user_id=str(user["_id"]),
        session=session,
        enabled=payload.enabled,
        workflow_names=payload.workflow_names,
        include_branches=payload.include_branches,
        exclude_branches=payload.exclude_branches,
        daily_investigation_limit=payload.daily_investigation_limit,
        github_check_reporting_enabled=payload.github_check_reporting_enabled,
    )
    return _settings_response(result)


@router.get(
    "/sessions/{slug_id}/automation",
    response_model=RepositoryAutomationSettingsResponse | None,
    response_model_exclude_none=True,
)
async def automation_settings(
    slug_id: str, user: dict = Depends(get_current_user)
) -> RepositoryAutomationSettingsResponse | None:
    session = await get_db().sessions.find_one({"userId": user["_id"], "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    document = await GitHubWebhookService().get_repository_settings(
        user_id=str(user["_id"]), session_id=str(session["_id"])
    )
    return _settings_response(document) if document else None


@router.put(
    "/sessions/{slug_id}/candidate-verification",
    response_model=CandidateVerificationSettingsResponse,
    response_model_exclude_none=True,
)
async def configure_candidate_verification(
    slug_id: str,
    payload: CandidateVerificationSettingsRequest,
    user: dict = Depends(get_current_user),
) -> CandidateVerificationSettingsResponse:
    """Bind an explicitly reviewed GitHub App workflow to one owned session."""

    session = await get_db().sessions.find_one({"userId": user["_id"], "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    document = await CandidateVerificationService().configure_repository(
        user_id=str(user["_id"]),
        session=session,
        enabled=payload.enabled,
        installation_id=payload.installation_id,
        workflow_path=payload.workflow_path,
    )
    return _candidate_verification_settings_response(document)


@router.get(
    "/sessions/{slug_id}/candidate-verification",
    response_model=CandidateVerificationSettingsResponse | None,
    response_model_exclude_none=True,
)
async def candidate_verification_settings(
    slug_id: str, user: dict = Depends(get_current_user)
) -> CandidateVerificationSettingsResponse | None:
    session = await get_db().sessions.find_one({"userId": user["_id"], "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    document = await CandidateVerificationService().get_repository_settings(
        user_id=str(user["_id"]), session_id=str(session["_id"])
    )
    return _candidate_verification_settings_response(document) if document else None


@router.get("/sessions/{slug_id}/candidate-verification/latest-run")
async def latest_candidate_verification_run(
    slug_id: str, user: dict = Depends(get_current_user)
) -> dict | None:
    """Return only the latest safe verification status for this owned session."""

    session = await get_db().sessions.find_one({"userId": user["_id"], "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return await CandidateVerificationService().get_latest_run(
        user_id=str(user["_id"]), session_id=str(session["_id"])
    )


@router.get("/sessions/{slug_id}/prevention-rules", response_model=list[PreventionRuleSummary])
async def list_prevention_rules(
    slug_id: str,
    limit: int = Query(default=30, ge=1, le=50),
    user: dict = Depends(get_current_user),
) -> list[PreventionRuleSummary]:
    """List read-only recurring-incident advisories for one owned session.

    This is a visibility endpoint, not an administrative mutation surface.
    Persisted rules remain non-blocking context for a human review and never
    affect validation, approval, delivery, or GitHub permissions.
    """

    session = await get_db().sessions.find_one({"userId": user["_id"], "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    records = await PreventionRuleService().list_for_session(
        user_id=str(user["_id"]), session_id=str(session["_id"]), limit=limit
    )
    return [_prevention_rule_response(record) for record in records]


@router.get("")
async def list_incidents(
    limit: int = Query(default=30, ge=1, le=100), user: dict = Depends(get_current_user)
) -> dict:
    cursor = (
        get_db()
        .incidents.find({"userId": user["_id"], "isGroupRoot": {"$ne": False}})
        .sort("updatedAt", -1)
        .limit(limit)
    )
    records = [_incident_response(record) async for record in cursor]
    return {"incidents": records}


@router.get("/fleet-intelligence")
async def organization_fleet_intelligence(
    limit: int = Query(default=50, ge=1, le=100), user: dict = Depends(get_current_user)
) -> dict:
    """Return observed/correlated fleet links, never causal assertions.

    The graph is scoped by the authenticated tenant ID. It exposes only the
    compact graph surface intended for a fleet view; raw evidence remains in
    the incident and export routes' existing tenant checks.
    """

    return await OrganizationIncidentGraphService().fleet_view(tenant_id=str(user["_id"]), limit=limit)


@router.get("/flaky-tests", response_model=list[FlakyTestObservationSummary])
async def list_flaky_test_observations(
    limit: int = Query(default=50, ge=1, le=100), user: dict = Depends(get_current_user)
) -> list[FlakyTestObservationSummary]:
    """List only the signed-in user's bounded historical observations."""

    cursor = (
        get_db()
        .flaky_test_observations.find({"userId": user["_id"]})
        .sort("updatedAt", -1)
        .limit(limit)
    )
    return [_flaky_observation_response(record) async for record in cursor]


@router.get("/verification-runs/{verification_run_id}")
async def get_candidate_verification_run(
    verification_run_id: str, user: dict = Depends(get_current_user)
) -> dict:
    return await CandidateVerificationService().get_run(
        user_id=str(user["_id"]), verification_run_id=verification_run_id
    )


@router.post("/verification-runs/{verification_run_id}/refresh")
async def refresh_candidate_verification_run(
    verification_run_id: str, user: dict = Depends(get_current_user)
) -> dict:
    """Perform a bounded App-scoped metadata refresh for an existing run."""

    return await CandidateVerificationService().observe_run(
        user_id=str(user["_id"]), verification_run_id=verification_run_id
    )


@router.get("/{incident_id}")
async def get_incident(incident_id: str, user: dict = Depends(get_current_user)) -> dict:
    incident = await get_db().incidents.find_one({"_id": incident_id, "userId": user["_id"]})
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return _incident_response(incident)


@router.put("/{incident_id}/feedback")
async def submit_incident_feedback(
    incident_id: str,
    payload: IncidentFeedbackRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Persist a redacted user annotation without altering diagnosis evidence.

    Feedback remains untrusted product input. It can guide a later human
    review, but cannot upgrade evidence strength, authorize a patch, or
    modify an existing approval binding.
    """

    correction = RedactionService().redact(payload.correction.strip())
    if payload.disposition == "incorrect" and len(correction) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Describe what appears incorrect so the diagnosis can be reviewed.",
        )
    feedback = {
        "disposition": payload.disposition,
        "correction": correction,
        "updatedAt": utc_now(),
        "untrusted": True,
    }
    result = await get_db().incidents.update_one(
        {"_id": incident_id, "userId": user["_id"]},
        {"$set": {"feedback": feedback, "updatedAt": utc_now()}},
    )
    if not result.matched_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    incident = await get_db().incidents.find_one({"_id": incident_id, "userId": user["_id"]})
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    session_id = incident.get("sessionId")
    repository_id = incident.get("repositoryId")
    maintenance_limitations: list[str] = []
    if payload.disposition == "incorrect" and isinstance(session_id, str) and isinstance(repository_id, str):
        try:
            rules = PreventionRuleService()
            await rules.invalidate_for_incident(
                user_id=str(user["_id"]),
                session_id=session_id,
                repository_id=repository_id,
                incident_id=incident_id,
            )
            await rules.refresh_for_repository(
                user_id=str(user["_id"]),
                session_id=session_id,
                repository_id=repository_id,
            )
        except Exception:
            # Feedback is valuable even when optional historical maintenance is
            # temporarily unavailable. Preserve the correction and surface a
            # bounded limitation instead of failing the browser request.
            maintenance_limitations.append("Prevention rules could not be reevaluated after this correction.")
    if payload.disposition == "incorrect":
        try:
            await _record_incorrect_feedback_memory(
                user_id=str(user["_id"]),
                incident_id=incident_id,
                incident=incident,
                correction=correction,
            )
        except Exception:
            maintenance_limitations.append("The correction could not be recorded in historical memory.")
    if maintenance_limitations:
        limitations = list(incident.get("limitations") or [])
        limitations.extend(item for item in maintenance_limitations if item not in limitations)
        await get_db().incidents.update_one(
            {"_id": incident_id, "userId": user["_id"]},
            {"$set": {"limitations": limitations, "updatedAt": utc_now()}},
        )
    return _incident_response(incident)


@router.get("/{incident_id}/export", response_model=None)
async def export_incident(
    incident_id: str,
    format: Literal["json", "markdown"] = Query(default="json"),
    user: dict = Depends(get_current_user),
) -> Response:
    """Export only redacted evidence that belongs to the requesting tenant."""

    incident = await get_db().incidents.find_one({"_id": incident_id, "userId": user["_id"]})
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    evidence_ids = incident_evidence_ids(incident)
    evidence = []
    if evidence_ids:
        evidence = await (
            get_db()
            .evidence.find(
                {
                    "id": {"$in": evidence_ids},
                    "userId": user["_id"],
                    "sessionId": incident["sessionId"],
                }
            )
            .to_list(len(evidence_ids))
        )
    export = build_incident_export(incident=incident, evidence=evidence)
    filename = f"base64-incident-{incident_id}"
    if format == "markdown":
        return PlainTextResponse(
            incident_export_markdown(export),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
        )
    return JSONResponse(
        jsonable_encoder(export),
        headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
    )
