"""Tenant-scoped incident automation configuration and read APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.db.mongo import get_db
from app.schemas.incident import (
    RepositoryAutomationSettingsRequest,
    RepositoryAutomationSettingsResponse,
)
from app.services.incident_service import GitHubWebhookService

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
        "createdAt",
        "updatedAt",
    }
    response = {key: value for key, value in document.items() if key in allowed}
    response["id"] = str(response.pop("_id"))
    return response


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


@router.get("/{incident_id}")
async def get_incident(incident_id: str, user: dict = Depends(get_current_user)) -> dict:
    incident = await get_db().incidents.find_one({"_id": incident_id, "userId": user["_id"]})
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return _incident_response(incident)
