"""Tenant-scoped incident automation configuration and read APIs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.deps import get_current_user
from app.db.mongo import get_db
from app.schemas.incident import (
    FlakyTestObservationSummary,
    IncidentFeedbackRequest,
    RepositoryAutomationSettingsRequest,
    RepositoryAutomationSettingsResponse,
)
from app.services.incident_export_service import (
    build_incident_export,
    incident_evidence_ids,
    incident_export_markdown,
)
from app.services.incident_service import GitHubWebhookService
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
        "feedback",
        "createdAt",
        "updatedAt",
    }
    response = {key: value for key, value in document.items() if key in allowed}
    response["id"] = str(response.pop("_id"))
    return response


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
