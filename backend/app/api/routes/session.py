from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.schemas.session import (
    ApprovalDecisionRequest,
    CreatePullRequestRequest,
    RunbookRequest,
    SessionChatRequest,
)
from app.services.session_service import (
    chat_stream,
    create_pull_request,
    create_runbook,
    decide_approval,
    get_run_replay,
    get_session_by_slug,
    get_user_sessions,
    list_evidence,
    list_runbooks,
    list_sources,
    reindex_session,
)

router = APIRouter()


@router.get("/all")
async def all_sessions(
    search: str | None = None,
    pageSize: int = Query(default=20, ge=1),
    pageNumber: int = Query(default=1, ge=1),
    user: dict = Depends(get_current_user),
) -> dict:
    return await get_user_sessions(user["_id"], search, pageSize, pageNumber)


@router.post("/chat")
async def chat(payload: SessionChatRequest, user: dict = Depends(get_current_user)) -> StreamingResponse:
    return StreamingResponse(
        chat_stream(user["_id"], payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/{slug_id}/approval/{approval_id}")
async def approval(
    slug_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return await decide_approval(user["_id"], slug_id, approval_id, payload)


@router.post("/{slug_id}/rag/reindex")
async def reindex(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await reindex_session(user["_id"], slug_id)


@router.get("/{slug_id}/rag/sources")
async def sources(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_sources(user["_id"], slug_id)


@router.get("/{slug_id}/evidence")
async def evidence(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_evidence(user["_id"], slug_id)


@router.get("/{slug_id}/runs/{run_id}")
async def run_replay(slug_id: str, run_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_run_replay(user["_id"], slug_id, run_id)


@router.post("/runbooks")
async def add_runbook(payload: RunbookRequest, user: dict = Depends(get_current_user)) -> dict:
    return await create_runbook(user["_id"], payload.title, payload.content, payload.tags)


@router.get("/runbooks")
async def runbooks(user: dict = Depends(get_current_user)) -> dict:
    return await list_runbooks(user["_id"])


@router.post("/{slug_id}/pr")
async def pull_request(
    slug_id: str,
    payload: CreatePullRequestRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return await create_pull_request(user["_id"], slug_id, payload)


@router.get("/{slug_id}")
async def by_slug(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_session_by_slug(user["_id"], slug_id)
