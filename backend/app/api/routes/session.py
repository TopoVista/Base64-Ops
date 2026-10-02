from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.schemas.session import (
    ApprovalDecisionRequest,
    CodeEditProposalRequest,
    CommitMessageUpdateRequest,
    CreatePullRequestRequest,
    RepositoryIndexRequest,
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
    get_session_dependency_graph,
    get_session_git_diff,
    get_session_git_status,
    get_user_sessions,
    list_evidence,
    list_runbooks,
    list_session_code_files,
    list_session_recent_commits,
    list_sources,
    propose_code_edit,
    read_session_code_file,
    refresh_latest_ci,
    reindex_session,
    update_delivery_commit_message,
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
async def reindex(
    slug_id: str,
    payload: RepositoryIndexRequest | None = None,
    user: dict = Depends(get_current_user),
) -> dict:
    return await reindex_session(
        user["_id"],
        slug_id,
        repo_url=payload.repoUrl if payload else None,
        default_branch=payload.defaultBranch if payload else None,
    )


@router.get("/{slug_id}/rag/sources")
async def sources(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_sources(user["_id"], slug_id)


@router.get("/{slug_id}/evidence")
async def evidence(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_evidence(user["_id"], slug_id)


@router.post("/{slug_id}/ci/refresh")
async def refresh_ci(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await refresh_latest_ci(user["_id"], slug_id)


@router.get("/{slug_id}/runs/{run_id}")
async def run_replay(slug_id: str, run_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_run_replay(user["_id"], slug_id, run_id)


@router.get("/{slug_id}/code/files")
async def code_files(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_session_code_files(user["_id"], slug_id)


@router.get("/{slug_id}/code/commits")
async def code_commits(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_session_recent_commits(user["_id"], slug_id)


@router.get("/{slug_id}/code/dependency-graph")
async def code_dependency_graph(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_session_dependency_graph(user["_id"], slug_id)


@router.get("/{slug_id}/code/file")
async def code_file(slug_id: str, path: str, user: dict = Depends(get_current_user)) -> dict:
    return await read_session_code_file(user["_id"], slug_id, path)


@router.get("/{slug_id}/code/git-status")
async def code_git_status(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_session_git_status(user["_id"], slug_id)


@router.get("/{slug_id}/code/git-diff")
async def code_git_diff(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_session_git_diff(user["_id"], slug_id)


@router.post("/{slug_id}/code/propose")
async def propose_code_change(
    slug_id: str,
    payload: CodeEditProposalRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return await propose_code_edit(user["_id"], slug_id, payload)


@router.patch("/{slug_id}/delivery-plan/{plan_id}/commit-message")
async def update_commit_message(
    slug_id: str,
    plan_id: str,
    payload: CommitMessageUpdateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return await update_delivery_commit_message(user["_id"], slug_id, plan_id, payload)


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
