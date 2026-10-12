from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.billing.usage_service import UsageService
from app.core.agent_run_guard import AgentRunGuard
from app.schemas.intelligence import ChangeImpactResponse, GraphNodeDetailResponse, GraphSnapshotStatus
from app.schemas.replay import RunReplayResponse, SessionRunHistoryResponse
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
    analyze_session_delivery_impact,
    chat_stream,
    create_pull_request,
    create_runbook,
    decide_approval,
    get_run_replay,
    get_session_by_slug,
    get_session_change_impact,
    get_session_dependency_graph,
    get_session_git_diff,
    get_session_git_status,
    get_session_intelligence_graph_node,
    get_session_intelligence_graph_status,
    get_user_sessions,
    list_evidence,
    list_runbooks,
    list_session_code_files,
    list_session_recent_commits,
    list_session_runs,
    list_sources,
    propose_code_edit,
    read_session_code_file,
    refresh_latest_ci,
    reindex_session,
    request_delivery_approval,
    update_delivery_commit_message,
)

router = APIRouter()
usage_service = UsageService()
agent_run_guard = AgentRunGuard()


@router.get("/all")
async def all_sessions(
    search: str | None = Query(default=None, max_length=100),
    pageSize: int = Query(default=20, ge=1, le=100),
    pageNumber: int = Query(default=1, ge=1, le=10_000),
    user: dict = Depends(get_current_user),
) -> dict:
    return await get_user_sessions(user["_id"], search, pageSize, pageNumber)


@router.post("/chat")
async def chat(payload: SessionChatRequest, user: dict = Depends(get_current_user)) -> StreamingResponse:
    # Reserve the bounded entitlement before a stream can invoke retrieval or
    # an LLM. The client cannot choose or increase this quota.
    lease = await agent_run_guard.acquire(str(user["_id"]))
    try:
        await usage_service.consume_agent_run(user, request_id=payload.clientRequestId)
    except Exception:
        await agent_run_guard.release(lease)
        raise

    async def guarded_stream():
        try:
            async for event in chat_stream(user["_id"], payload):
                yield event
        finally:
            await agent_run_guard.release(lease)

    return StreamingResponse(
        guarded_stream(),
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


@router.get("/{slug_id}/runs/{run_id}", response_model=RunReplayResponse, response_model_exclude_none=True)
async def run_replay(slug_id: str, run_id: str, user: dict = Depends(get_current_user)) -> RunReplayResponse:
    return await get_run_replay(user["_id"], slug_id, run_id)


@router.get("/{slug_id}/runs", response_model=SessionRunHistoryResponse, response_model_exclude_none=True)
async def runs(
    slug_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(get_current_user),
) -> SessionRunHistoryResponse:
    return await list_session_runs(user["_id"], slug_id, limit)


@router.get("/{slug_id}/code/files")
async def code_files(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_session_code_files(user["_id"], slug_id)


@router.get("/{slug_id}/code/commits")
async def code_commits(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await list_session_recent_commits(user["_id"], slug_id)


@router.get("/{slug_id}/code/dependency-graph")
async def code_dependency_graph(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_session_dependency_graph(user["_id"], slug_id)


@router.get("/{slug_id}/intelligence/graph", response_model=GraphSnapshotStatus)
@router.get("/{slug_id}/intelligence/graph/status", response_model=GraphSnapshotStatus)
async def intelligence_graph_status(
    slug_id: str,
    commitSha: str | None = Query(default=None, min_length=7, max_length=64),
    user: dict = Depends(get_current_user),
) -> GraphSnapshotStatus:
    return await get_session_intelligence_graph_status(user["_id"], slug_id, commitSha)


@router.get("/{slug_id}/intelligence/graph/nodes/{node_id}", response_model=GraphNodeDetailResponse)
async def intelligence_graph_node(
    slug_id: str,
    node_id: str,
    commitSha: str | None = Query(default=None, min_length=7, max_length=64),
    depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=100, ge=1, le=250),
    user: dict = Depends(get_current_user),
) -> GraphNodeDetailResponse:
    return await get_session_intelligence_graph_node(
        user["_id"], slug_id, node_id, commit_sha=commitSha, depth=depth, limit=limit
    )


@router.post("/{slug_id}/delivery-plan/{plan_id}/impact", response_model=ChangeImpactResponse)
async def analyze_delivery_impact(
    slug_id: str,
    plan_id: str,
    user: dict = Depends(get_current_user),
) -> ChangeImpactResponse:
    return await analyze_session_delivery_impact(user["_id"], slug_id, plan_id)


@router.get("/{slug_id}/intelligence/impact/{analysis_id}", response_model=ChangeImpactResponse)
async def change_impact(
    slug_id: str,
    analysis_id: str,
    user: dict = Depends(get_current_user),
) -> ChangeImpactResponse:
    return await get_session_change_impact(user["_id"], slug_id, analysis_id)


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


@router.post("/{slug_id}/delivery-plan/{plan_id}/approval")
async def request_plan_approval(
    slug_id: str,
    plan_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Create a new exact approval record; it never executes a delivery."""

    return await request_delivery_approval(user["_id"], slug_id, plan_id)


@router.post("/runbooks")
async def add_runbook(payload: RunbookRequest, user: dict = Depends(get_current_user)) -> dict:
    return await create_runbook(user["_id"], payload.title, payload.content, payload.tags)


@router.get("/runbooks")
async def runbooks(user: dict = Depends(get_current_user)) -> dict:
    return await list_runbooks(user["_id"])


@router.post("/{slug_id}/pr", deprecated=True)
async def pull_request(
    slug_id: str,
    payload: CreatePullRequestRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    return await create_pull_request(user["_id"], slug_id, payload)


@router.get("/{slug_id}")
async def by_slug(slug_id: str, user: dict = Depends(get_current_user)) -> dict:
    return await get_session_by_slug(user["_id"], slug_id)
