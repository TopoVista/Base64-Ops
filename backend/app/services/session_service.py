import asyncio
import logging
import math
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.agent.graph import AgentGraph
from app.billing.usage_service import UsageService
from app.core.config import get_settings
from app.db.mongo import get_db
from app.git.actions import GitHubActionsAdapter
from app.intelligence.graph.service import EngineeringGraphService
from app.intelligence.impact.models import ChangeImpactResponse
from app.intelligence.impact.service import ChangeImpactService
from app.observability.recorder import MongoTraceExporter, TraceRecorder
from app.schemas.delivery import DeliveryPlan
from app.schemas.patch import FileEditIntent, PatchProposal
from app.schemas.replay import RunReplayResponse, SessionRunHistoryResponse
from app.schemas.session import (
    ApprovalDecisionRequest,
    CodeEditProposalRequest,
    CommitMessageUpdateRequest,
    CreatePullRequestRequest,
    SessionChatRequest,
)
from app.services.background_job_service import BackgroundJobService
from app.services.ci_investigation_service import CIInvestigationService
from app.services.ci_request_intent import extract_ci_request_intent
from app.services.ci_run_resolution_service import CIRunResolutionService
from app.services.delivery_service import DeliveryService, content_hash
from app.services.evidence_service import EvidenceService
from app.services.github_service import resolve_authorized_repository
from app.services.patch_engine import PatchEngine, PatchSafetyError
from app.services.rag_service import RagService
from app.services.redaction_service import RedactionService
from app.services.serializers import serialize_doc
from app.services.workspace_service import WorkspaceService, branch_name_from_prompt, repo_name_from_url
from app.utils.datetime import utc_now
from app.utils.ids import new_id
from app.utils.sse import sse_event

agent_graph = AgentGraph()
redactor = RedactionService()
logger = logging.getLogger(__name__)
CI_REFRESH_INTERVAL = timedelta(minutes=1)
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")

# The run inspector only needs provenance metadata for its evidence list.  Do
# not hydrate excerpts, chunks, or other source-bearing fields merely to draw
# that list: the regular evidence view owns the bounded, redacted inspection
# experience when an operator explicitly asks for it.
_REPLAY_EVIDENCE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "path": 1,
    "source": 1,
    "title": 1,
    "sourceType": 1,
    "source_type": 1,
    "retrievalMethod": 1,
    "retrieval_method": 1,
    "retrievalScore": 1,
    "score": 1,
    "commitSha": 1,
    "commit_sha": 1,
    "metadata.state": 1,
    "metadata.truncated": 1,
}

# Historical records predate the current candidate-workspace limits in some
# installations. Keep the replay UI responsive even if one such record holds
# a multi-megabyte unified diff, and mark any preview honestly in the public
# contract instead of silently presenting it as the exact approved patch.
_MAX_REPLAY_DIFF_BYTES = 256_000


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """Return only object entries from an optional persisted sequence."""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_items(value: Any) -> list[str]:
    """Normalize legacy limitation/tag fields without iterating a string by character."""

    if isinstance(value, str):
        return [value] if value else []
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _safe_replay_text(value: Any) -> str | None:
    """Keep malformed historical fields from invalidating an entire replay."""

    return value if isinstance(value, str) else None


def _bounded_replay_text(value: Any, max_bytes: int) -> tuple[str | None, bool]:
    """Return a UTF-8-safe bounded replay value and whether it was shortened."""

    text = _safe_replay_text(value)
    if text is None:
        return None, False
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # Decode with an incomplete final code point omitted so a browser always
    # receives valid text. The separate flag preserves the exactness boundary.
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _safe_replay_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_replay_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _safe_replay_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_replay_timestamp(value: Any) -> str | None:
    """Serialize a persisted date only when it has a browser-safe string form."""

    return _safe_replay_text((serialize_doc({"value": value}) or {}).get("value"))


async def ensure_session_indexes() -> None:
    """Install tenant and timeline indexes used by the interactive API."""

    db = get_db()
    await db.sessions.create_index(
        [("userId", 1), ("slugId", 1)],
        unique=True,
        name="sessions_one_slug_per_tenant",
    )
    await db.sessions.create_index([("userId", 1), ("createdAt", -1)])
    await db.messages.create_index([("sessionId", 1), ("createdAt", 1)])
    await db.investigations.create_index([("userId", 1), ("sessionId", 1), ("createdAt", -1)])
    await db.investigations.create_index(
        [("userId", 1), ("sessionId", 1), ("runId", 1)],
        unique=True,
        name="investigations_one_run_per_tenant_session",
    )
    await db.evidence.create_index([("userId", 1), ("sessionId", 1), ("runId", 1), ("retrievedAt", 1)])
    await db.run_traces.create_index([("user_id", 1), ("session_id", 1), ("run_id", 1)])
    await db.trace_spans.create_index([("trace_id", 1), ("started_at", 1)])
    await db.delivery_plans.create_index([("user_id", 1), ("session_id", 1), ("run_id", 1)])
    await db.approvals.create_index([("user_id", 1), ("session_id", 1), ("run_id", 1), ("created_at", -1)])


def ci_refresh_due(value: Any, *, interval: timedelta = CI_REFRESH_INTERVAL) -> bool:
    """Return whether a configured, opt-in Actions discovery interval elapsed."""
    if not value:
        return True
    checked_at = value
    if isinstance(value, str):
        try:
            checked_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return True
    if not isinstance(checked_at, datetime):
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - checked_at >= interval


def safe_tool_result(value: Any) -> dict[str, Any] | None:
    """Keep a compact, redacted diagnostic outcome safe for SSE and persistence."""
    if not isinstance(value, dict):
        return None
    return {
        "success": bool(value.get("success")),
        "output": redactor.redact(value.get("output", "") if isinstance(value.get("output"), str) else "")[:4_000],
    }


def index_failure_reason(exc: Exception) -> str:
    """Return an actionable, secret-safe explanation for repository indexing failures."""
    if isinstance(exc, PyMongoError):
        return (
            "RAG storage could not connect to MongoDB. Check Atlas network access, TLS settings, "
            "and the configured MongoDB credentials, then retry indexing."
        )
    return (
        "Indexing could not complete after the repository workspace was prepared. "
        "The agent can still use bounded direct read-only inspection for this session."
    )


def extract_prompt(payload: SessionChatRequest) -> str:
    if payload.message:
        return payload.message
    if payload.messages:
        last = payload.messages[-1]
        if isinstance(last, dict):
            if isinstance(last.get("content"), str):
                return last["content"]
            parts = last.get("parts")
            if isinstance(parts, list):
                texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
                return "\n".join(text for text in texts if text)
    return ""


async def get_or_create_session(user_id: str, payload: SessionChatRequest, prompt: str) -> dict[str, Any]:
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": payload.slugId})
    if session:
        return session
    authorized_repository = await resolve_authorized_repository(user_id, payload.repoUrl)
    repo_url = authorized_repository["cloneUrl"]
    repo_name = repo_name_from_url(repo_url)
    default_branch = payload.defaultBranch or authorized_repository["defaultBranch"]
    if (
        not _SAFE_BRANCH.fullmatch(default_branch)
        or ".." in default_branch
        or "//" in default_branch
        or default_branch.endswith((".", "/"))
        or "@{" in default_branch
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid repository branch.")
    now = utc_now()
    session = {
        "_id": new_id("ses_"),
        "userId": user_id,
        "slugId": payload.slugId,
        "title": prompt[:48] or "Untitled Session",
        "repoUrl": repo_url,
        "repoName": repo_name,
        "defaultBranch": default_branch,
        "branchName": branch_name_from_prompt(prompt),
        "status": "active",
        "repoInitializedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        await db.sessions.insert_one(session)
    except DuplicateKeyError:
        # A different API replica may have created this exact tenant session
        # while we verified the repository. Never create a second workspace or
        # overwrite the existing session's repository binding.
        existing = await db.sessions.find_one({"userId": user_id, "slugId": payload.slugId})
        if existing:
            return existing
        raise
    return session


async def append_message(
    session_id: str,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message = {
        "_id": new_id("msg_"),
        "id": new_id("msg_"),
        "sessionId": session_id,
        "role": role,
        "content": content,
        "sources": sources or [],
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
    }
    await get_db().messages.insert_one(message)
    return message


async def get_user_sessions(user_id: str, search: str | None, page_size: int, page_number: int) -> dict:
    query: dict[str, Any] = {"userId": user_id}
    if search:
        # Browser search must never become an arbitrary Mongo regular
        # expression. Literal escaping protects the database from regex DoS.
        safe_search = re.escape(search[:100])
        query["$or"] = [
            {"title": {"$regex": safe_search, "$options": "i"}},
            {"repoName": {"$regex": safe_search, "$options": "i"}},
        ]
    skip = (page_number - 1) * page_size
    db = get_db()
    sessions = await db.sessions.find(query).sort("createdAt", -1).skip(skip).limit(page_size).to_list(page_size)
    total = await db.sessions.count_documents(query)
    return {
        "sessions": [serialize_doc(session) for session in sessions],
        "pagination": {
            "pageSize": page_size,
            "pageNumber": page_number,
            "totalCount": total,
            "totalPages": (total + page_size - 1) // page_size,
            "skip": skip,
        },
    }


async def get_session_by_slug(user_id: str, slug_id: str) -> dict:
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    # Browser session state is fetched repeatedly. Automatic Actions discovery
    # is therefore opt-in; explicit CI refresh and post-delivery verification
    # remain the normal ways to contact GitHub. This prevents page polling from
    # multiplying workspace/API work as the tenant count grows.
    auto_refresh_seconds = get_settings().ci_auto_refresh_seconds
    if (
        auto_refresh_seconds > 0
        and session.get("repoUrl")
        and ci_refresh_due(
            session.get("ciLastCheckedAt"),
            interval=timedelta(seconds=auto_refresh_seconds),
        )
    ):
        try:
            await refresh_latest_ci(user_id, slug_id)
            refreshed = await db.sessions.find_one({"_id": session["_id"]})
            if refreshed:
                session = refreshed
        except Exception:
            await db.sessions.update_one(
                {"_id": session["_id"]},
                {"$set": {"ciLastCheckedAt": utc_now()}},
            )
    messages = await db.messages.find({"sessionId": session["_id"]}).sort("createdAt", 1).to_list(500)
    latest = await db.investigations.find_one(
        {"userId": user_id, "sessionId": session["_id"]}, sort=[("createdAt", -1)]
    )
    command_center = None
    if latest:
        # Delivery results are mutable only for read-only follow-up state such
        # as CI verification. Load the canonical delivery record rather than
        # serving a stale nested snapshot from the original investigation.
        delivery_result = latest.get("deliveryResult")
        delivery_plan = latest.get("deliveryPlan") or {}
        plan_id = delivery_plan.get("id")
        approvals = latest.get("approvals", [])
        if plan_id:
            current_delivery = await db.delivery_results.find_one(
                {"user_id": user_id, "delivery_plan_id": plan_id}, sort=[("created_at", -1)]
            )
            if current_delivery:
                delivery_result = current_delivery
            # Approval records are mutable: a reviewer can reject one, a
            # commit-message edit can invalidate it, and a replacement
            # approval can be created later. The nested investigation copy is
            # display context only, so resolve the canonical plan-bound
            # pending record before presenting the Code workspace.
            current_approval = await db.approvals.find_one(
                {
                    "userId": user_id,
                    "sessionId": session["_id"],
                    "deliveryPlanId": plan_id,
                    "status": "pending",
                },
                sort=[("createdAt", -1)],
            )
            if current_approval:
                snapshot = next(
                    (
                        item
                        for item in approvals
                        if isinstance(item, dict) and item.get("id") == current_approval.get("id")
                    ),
                    {},
                )
                # Preserve safe display metadata from the snapshot while the
                # canonical record controls status and immutable binding.
                approvals = [{**snapshot, **current_approval}]
        command_center = {
            "runId": latest.get("runId"),
            "repository": latest.get("repository", {}),
            "investigation": latest.get("investigation"),
            "memory": latest.get("memory", []),
            "ci": latest.get("ci"),
            "timeline": latest.get("timeline", []),
            "deliveryPlan": delivery_plan or None,
            "deliveryResult": delivery_result,
            "toolResult": latest.get("toolResult"),
            "approvals": approvals,
            "evidence": latest.get("evidence", []),
        }
    return {
        "session": serialize_doc(session),
        "messages": [serialize_doc(message) for message in messages],
        "commandCenter": command_center,
    }


async def list_session_code_files(user_id: str, slug_id: str) -> dict:
    """Return a bounded, read-only file tree for the authenticated session repository."""
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(user_id, slug_id, session["repoUrl"], session.get("defaultBranch"))
    files = []
    for path in workspace.iter_indexable_files(repo_path):
        files.append(
            {
                "path": str(path.relative_to(repo_path)).replace("\\", "/"),
                "bytes": path.stat().st_size,
            }
        )
    return {"headSha": workspace.head_commit(repo_path), "files": files}


async def get_session_git_status(user_id: str, slug_id: str) -> dict:
    """Return git status --short output for the session workspace (read-only)."""
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(user_id, slug_id, session["repoUrl"], session.get("defaultBranch"))
    result = workspace.git_status(repo_path)
    redactor = RedactionService()
    return {
        "headSha": workspace.head_commit(repo_path),
        "branch": workspace.current_branch(repo_path),
        "output": redactor.redact(result.get("output", "")) if result.get("success") else "",
        "success": result.get("success", False),
    }


async def get_session_git_diff(user_id: str, slug_id: str) -> dict:
    """Return git diff (staged + unstaged) for the session workspace (read-only)."""
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(user_id, slug_id, session["repoUrl"], session.get("defaultBranch"))
    # Full unified diff is bounded and redacted before returning.
    result = workspace._run(["git", "diff", "--unified=3"], cwd=repo_path)
    redactor = RedactionService()
    raw = result.get("output", "") if result.get("success") else ""
    return {
        "headSha": workspace.head_commit(repo_path),
        "branch": workspace.current_branch(repo_path),
        "diff": redactor.redact(raw[:80_000]),  # bounded: never send huge diffs to the browser
        "truncated": len(raw) > 80_000,
        "success": result.get("success", False),
    }


async def list_session_recent_commits(user_id: str, slug_id: str) -> dict:
    """Return bounded, read-only commit metadata for the session Code view."""
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(user_id, slug_id, session["repoUrl"], session.get("defaultBranch"))
    workspace.hydrate_recent_history(repo_path, session.get("defaultBranch"))
    commits = workspace.recent_commits(repo_path)

    # Actions status is optional read-only presentation data. A missing OAuth
    # grant, transient GitHub issue, or rate limit must never hide local commit
    # history or turn it into a write capability.
    try:
        # A reviewed delivery intentionally runs on its own branch. Query that
        # branch instead of the session's original default branch so the right
        # IDE rail can show the real Actions result for the commit the user
        # just approved and pushed.
        active_branch = workspace.current_branch(repo_path) or session.get("defaultBranch")
        runs = await GitHubActionsAdapter().list_workflow_runs(
            user_id=user_id,
            repository_id=session["repoUrl"],
            branch=active_branch,
        )
        newest_by_sha: dict[str, Any] = {}
        for run in runs:
            existing = newest_by_sha.get(run.head_sha)
            is_newer = (
                existing is not None
                and run.updated_at
                and (not existing.updated_at or run.updated_at > existing.updated_at)
            )
            if existing is None or is_newer:
                newest_by_sha[run.head_sha] = run
        for commit in commits:
            run = newest_by_sha.get(commit["sha"])
            commit["ci"] = (
                {
                    "status": run.status,
                    "conclusion": run.conclusion,
                    "workflow": run.workflow_name,
                    "url": run.html_url,
                }
                if run
                else {"status": "not_observed", "conclusion": None}
            )
    except Exception:
        for commit in commits:
            commit["ci"] = {"status": "unavailable", "conclusion": None}

    return {"headSha": workspace.head_commit(repo_path), "commits": commits}


async def get_session_dependency_graph(user_id: str, slug_id: str) -> dict:
    """Return a tenant-scoped, static local-import graph for the Code view."""
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(user_id, slug_id, session["repoUrl"], session.get("defaultBranch"))
    return workspace.dependency_graph(repo_path)


async def get_session_intelligence_graph_status(user_id: str, slug_id: str, commit_sha: str | None = None):
    """Return only graph-build metadata; never construct a graph during a GET."""
    from app.schemas.intelligence import GraphSnapshotStatus

    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    snapshot = await EngineeringGraphService().snapshot_for_session(
        user_id=user_id, session=session, commit_sha=commit_sha
    )
    return GraphSnapshotStatus.from_snapshot(snapshot)


async def get_session_intelligence_graph_node(
    user_id: str,
    slug_id: str,
    node_id: str,
    *,
    commit_sha: str | None = None,
    depth: int = 1,
    limit: int = 100,
):
    """Return a bounded, tenant-scoped local graph neighborhood."""
    from app.schemas.intelligence import GraphEdgePublic, GraphNodeDetailResponse, GraphNodePublic, GraphSnapshotStatus

    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    graph_service = EngineeringGraphService()
    snapshot = await graph_service.snapshot_for_session(user_id=user_id, session=session, commit_sha=commit_sha)
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No engineering graph is available for this repository revision. Reindex the repository first.",
        )
    nodes, edges, truncated = graph_service.neighborhood(snapshot, node_id=node_id, depth=depth, limit=limit)
    selected = next(node for node in nodes if node.id == node_id)
    return GraphNodeDetailResponse(
        snapshot=GraphSnapshotStatus.from_snapshot(snapshot),
        node=GraphNodePublic.from_node(selected),
        nodes=[GraphNodePublic.from_node(node) for node in nodes],
        edges=[GraphEdgePublic.from_edge(edge) for edge in edges],
        truncated=truncated,
    )


async def analyze_session_delivery_impact(
    user_id: str,
    slug_id: str,
    plan_id: str,
) -> ChangeImpactResponse:
    """Analyze a persisted exact proposal against its matching graph revision.

    This is deliberately separate from proposal generation and approval.  It
    has no write path to a repository, no access to candidate source content,
    and will refuse to combine a plan with a graph from another SHA.
    """

    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    plan_document = await db.delivery_plans.find_one(
        {"id": plan_id, "userId": user_id, "sessionId": session["_id"]}
    )
    if not plan_document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")
    plan = DeliveryPlan.model_validate(plan_document)
    if plan.repository_id != session.get("repoUrl"):
        # A valid plan can never be applied to a different session repository;
        # keep the response indistinguishable from a missing plan to avoid
        # disclosing repository association across session boundaries.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")
    snapshot = await EngineeringGraphService().snapshot_for_session(
        user_id=user_id,
        session=session,
        commit_sha=plan.base_sha,
    )
    analysis_service = ChangeImpactService()
    traces = getattr(db, "run_traces", None)
    trace = (
        await traces.find_one({"user_id": user_id, "session_id": session["_id"], "run_id": plan.run_id})
        if traces is not None
        else None
    )
    trace_id = trace.get("id") if isinstance(trace, dict) else None
    if trace_id:
        async with TraceRecorder(MongoTraceExporter()).span(
            str(trace_id),
            "impact.analysis",
            {
                "delivery_plan_id": plan.id,
                "changed_file_count": len(plan.files),
                "graph_available": bool(snapshot),
            },
        ) as span:
            analysis = await analysis_service.analyze_and_store(
                user_id=user_id,
                session_id=str(session["_id"]),
                repository_id=plan.repository_id,
                plan=plan,
                snapshot=snapshot,
            )
            span.safe_output_summary = {
                "status": analysis.status.value,
                "direct_count": len(analysis.directly_affected),
                "indirect_count": len(analysis.indirectly_affected),
                "test_count": len(analysis.related_tests),
                "workflow_count": len(analysis.related_workflows),
                "truncated": analysis.truncated,
            }
    else:
        analysis = await analysis_service.analyze_and_store(
            user_id=user_id,
            session_id=str(session["_id"]),
            repository_id=plan.repository_id,
            plan=plan,
            snapshot=snapshot,
        )
    return ChangeImpactResponse.from_analysis(analysis)


async def get_session_change_impact(
    user_id: str,
    slug_id: str,
    analysis_id: str,
) -> ChangeImpactResponse:
    """Return a historical impact analysis only to its original session owner."""

    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    analysis = await ChangeImpactService().store.by_id(
        user_id=user_id,
        session_id=str(session["_id"]),
        analysis_id=analysis_id,
    )
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Impact analysis not found")
    return ChangeImpactResponse.from_analysis(analysis)


async def read_session_code_file(user_id: str, slug_id: str, path: str) -> dict:
    """Read one safe repository file through the existing workspace boundary.

    This endpoint deliberately has no write counterpart. Content is bounded and sent through
    centralized redaction before it reaches the browser.
    """
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(user_id, slug_id, session["repoUrl"], session.get("defaultBranch"))
    content = workspace.read_file(repo_path, path)
    max_chars = 64_000
    truncated = len(content) > max_chars
    displayed_content = redactor.redact(content[:max_chars])
    contains_redactions = displayed_content != content[:max_chars]
    return {
        "path": path,
        "headSha": workspace.head_commit(repo_path),
        "contentHash": content_hash(content),
        "content": displayed_content,
        "truncated": truncated,
        # Never let a redacted browser representation become a replacement for
        # the real source file. It remains readable, but is proposal-locked.
        "containsRedactions": contains_redactions,
        "editable": not truncated and not contains_redactions,
    }


async def propose_code_edit(user_id: str, slug_id: str, payload: CodeEditProposalRequest) -> dict:
    """Turn one browser edit into the normal validated, approval-bound plan.

    The browser never writes a workspace. Its submitted text is untrusted draft
    input. We first verify the exact current file hash, create current-source
    evidence, then let the existing patch engine produce the candidate diff,
    risk classification, validation results, and exact approval binding.
    """
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch") or "main"
    )
    try:
        current_content = workspace.read_file(repo_path, payload.path)
    except HTTPException:
        raise
    if redactor.redact(current_content) != current_content:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Files containing redacted values cannot be edited from the browser.",
        )
    if content_hash(current_content) != payload.expectedOriginalHash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The source file changed after it was opened. Reload it and review the edit again.",
        )

    run_id = new_id("run_")
    source_records = await EvidenceService().record_sources(
        user_id=user_id,
        session={
            "_id": session["_id"],
            "repoUrl": session["repoUrl"],
            "defaultBranch": session.get("defaultBranch"),
        },
        run_id=run_id,
        sources=[
            {
                "kind": "repo",
                "source": payload.path,
                "excerpt": current_content,
                "metadata": {"state": "current", "origin": "code_editor", "untrusted": True},
            }
        ],
        commit_sha=workspace.head_commit(repo_path),
    )
    evidence_ids = [record["id"] for record in source_records]
    summary = (payload.commitMessage or f"Update {payload.path}").strip()
    proposal = PatchProposal(
        summary=summary,
        rationale="User-authored code draft, constrained to the currently evidenced repository file.",
        evidence_ids=evidence_ids,
        edits=[
            FileEditIntent(
                path=payload.path,
                operation="modify",
                reason="User-authored draft requires exact review and approval.",
                evidence_ids=evidence_ids,
                expected_original_hash=payload.expectedOriginalHash,
                proposed_content=payload.proposedContent,
            )
        ],
    )
    repository_map = workspace.repository_map(repo_path)
    try:
        plan, surfaces = PatchEngine().build_delivery_plan(
            proposal=proposal,
            user_id=user_id,
            session_id=session["_id"],
            run_id=run_id,
            repository_id=session["repoUrl"],
            base_branch=workspace.current_branch(repo_path) or session.get("defaultBranch") or "main",
            base_sha=workspace.head_commit(repo_path) or "",
            repo_path=repo_path,
            evidence=source_records,
            repo_map=repository_map,
        )
    except PatchSafetyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    delivery = DeliveryService()
    validation = delivery.validate_plan(plan, repo_path)
    if any(result.status in {"failed", "error"} and result.step_id != "review" for result in validation):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The proposed edit did not pass its required pre-approval validation.",
        )
    plan_document = await delivery.persist_plan(plan)
    approval = await delivery.create_approval(plan)
    await db.investigations.update_one(
        {"userId": user_id, "sessionId": session["_id"], "runId": run_id},
        {
            "$set": {
                "userId": user_id,
                "sessionId": session["_id"],
                "runId": run_id,
                "repository": {
                    "name": session.get("repoName"),
                    "branch": plan.base_branch,
                    "commit_sha": plan.base_sha,
                    "file_count": repository_map["fileCount"],
                    "ci_files": repository_map["ci"],
                    "manifests": repository_map["manifests"],
                },
                "investigation": None,
                "memory": [],
                "ci": None,
                "timeline": [
                    {
                        "id": new_id("evt_"),
                        "label": "Code edit proposed",
                        "detail": payload.path,
                        "status": "completed",
                    },
                    {
                        "id": new_id("evt_"),
                        "label": "Risk classified",
                        "detail": plan.risk_level.upper(),
                        "status": "completed",
                    },
                    {
                        "id": new_id("evt_"),
                        "label": "Approval requested",
                        "detail": "Exact diff is ready for review",
                        "status": "completed",
                    },
                ],
                "deliveryPlan": plan_document,
                "toolResult": None,
                "approvals": [
                    {
                        **approval,
                        "action": "Create reviewable code edit",
                        "summary": plan.rationale,
                        "risk": "approval_required",
                        "riskLevel": plan.risk_level,
                        "baseBranch": plan.base_branch,
                        "baseSha": plan.base_sha,
                        "files": [item.model_dump(exclude={"proposed_content"}) for item in plan.files],
                        "validation": [item.model_dump() for item in validation],
                        "evidenceIds": evidence_ids,
                    }
                ],
                "evidence": [serialize_doc(item) for item in source_records],
                "createdAt": utc_now(),
            }
        },
        upsert=True,
    )
    await db.sessions.update_one({"_id": session["_id"]}, {"$set": {"latestRunId": run_id}})
    return {
        "deliveryPlan": plan_document,
        "approval": {
            **approval,
            "riskLevel": plan.risk_level,
            "validation": [item.model_dump() for item in validation],
        },
        "changeSurfaces": [item.model_dump() for item in surfaces],
    }


def extract_interrupt(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        first = interrupts[0]
        return getattr(first, "value", None) or first.get("value") if isinstance(first, dict) else first
    return None


async def chat_stream(
    user_id: str,
    payload: SessionChatRequest,
    *,
    persist_messages: bool = True,
    force_delivery_plan: bool = False,
) -> AsyncIterator[str]:
    """Run the evidence graph for a session.

    ``persist_messages`` is deliberately opt-out only for first-party, focused
    workflows such as the Code workbench's CI repair.  Those workflows need
    the normal investigation, approval, trace, and delivery records, but are
    not conversational turns and must not add an opaque internal prompt or a
    generated report to the user's chat transcript.
    """
    prompt = extract_prompt(payload).strip()
    if not prompt:
        yield sse_event("error", {"message": "Message is required"})
        return
    trace = None
    try:
        session = await get_or_create_session(user_id, payload, prompt)
        if persist_messages:
            await append_message(session["_id"], "user", prompt)
        yield sse_event("session.updated", {"session": serialize_doc(session)})
        yield sse_event("tool.started", {"name": "langgraph", "label": "Starting LangGraph run"})

        run_id = new_id("run_")
        recorder = TraceRecorder(MongoTraceExporter())
        trace = await recorder.start_run(user_id, session["_id"], run_id, session["repoUrl"])
        initial_state = {
            "user_id": user_id,
            "session_id": session["_id"],
            "slug_id": session["slugId"],
            "repo_url": session["repoUrl"],
            "default_branch": session.get("defaultBranch") or "main",
            "branch_name": session.get("branchName") or branch_name_from_prompt(prompt),
            "prompt": prompt,
            # Explicitly write None for ordinary chats too. LangGraph keeps
            # session state, so omitting this field could accidentally retain
            # a target from an earlier CI remediation request.
            "patch_target_path": payload.patchTargetPath,
            "run_id": run_id,
            "trace_id": trace.id,
            "workbench_mode": not persist_messages,
            "force_delivery_plan": force_delivery_plan,
        }
        emitted_timeline_ids: set[str] = set()
        async with recorder.span(
            trace.id, "agent.run", {"prompt_chars": len(prompt), "session_id": session["_id"]}
        ) as span:
            async for update in agent_graph.stream(initial_state):
                for node_name, node_state in update.items():
                    if node_name == "__interrupt__" or not isinstance(node_state, dict):
                        continue
                    for item in node_state.get("timeline", []):
                        event_id = str(item.get("id", ""))
                        if event_id and event_id in emitted_timeline_ids:
                            continue
                        if event_id:
                            emitted_timeline_ids.add(event_id)
                        yield sse_event("tool.completed", item)
            result = await agent_graph.current_state(session["slugId"])
            # Provider usage comes only from the completed graph state; never
            # accept token counts from the browser. A graph can invoke both a
            # patch-proposal model and its final-answer model, so meter every
            # captured provider event rather than silently retaining only the
            # final call. This is best-effort observability, while pre-run
            # entitlement and context ceilings remain the enforceable spend
            # boundary.
            try:
                usage_events = result.get("model_usage_events")
                if isinstance(usage_events, list):
                    for usage_event in usage_events:
                        await UsageService().record_model_usage(user_id, usage_event)
                else:
                    # Backward-compatible state produced before the usage
                    # event field existed.
                    await UsageService().record_model_usage(user_id, result.get("model_usage"))
            except Exception:
                pass
            span.safe_output_summary = {
                "timeline_events": len(result.get("timeline", [])),
                "model_usage": result.get("model_usage", {}),
            }

        interrupt = extract_interrupt(result)

        repository_map = result.get("repository_map") or {}
        if repository_map:
            yield sse_event(
                "repository.context",
                {
                    "repository": {
                        "name": result.get("repo_name"),
                        "branch": result.get("default_branch"),
                        "commit_sha": result.get("repo_commit_sha"),
                        "file_count": repository_map.get("fileCount", 0),
                        "ci_files": repository_map.get("ci", []),
                        "manifests": repository_map.get("manifests", []),
                        "capabilities": repository_map.get("capabilities", []),
                    }
                },
            )
        investigation = result.get("investigation")
        if investigation:
            yield sse_event("investigation.result", {"investigation": investigation})
        memory_items = list(result.get("operational_memory", []))
        memory_items.extend((result.get("context_pack") or {}).get("historical_memory", []))
        if memory_items:
            yield sse_event("memory.related", {"items": memory_items})
        plan = result.get("delivery_plan")
        if plan:
            yield sse_event("delivery.plan", {"plan": plan})
        tool_result = safe_tool_result(result.get("tool_result"))
        if tool_result:
            yield sse_event("tool.result", {"result": tool_result})

        sources = result.get("sources", [])
        evidence = result.get("evidence", [])
        if evidence:
            yield sse_event("evidence.items", {"evidence": [serialize_doc(item) for item in evidence]})
        if result.get("ci_context"):
            yield sse_event("ci.summary", {"ci": result["ci_context"]})
        if sources:
            yield sse_event("rag.sources", {"sources": sources})
        approval_payload = None
        if interrupt:
            approval_payload = {
                **interrupt,
                "sessionId": session["_id"],
                "slugId": session["slugId"],
                "status": "pending",
                "createdAt": utc_now(),
                "updatedAt": utc_now(),
            }
            await get_db().approvals.update_one({"id": approval_payload["id"]}, {"$set": approval_payload}, upsert=True)

        repository_context = {
            "name": result.get("repo_name"),
            "branch": result.get("default_branch"),
            "commit_sha": result.get("repo_commit_sha"),
            "file_count": repository_map.get("fileCount", 0),
            "ci_files": repository_map.get("ci", []),
            "manifests": repository_map.get("manifests", []),
            "capabilities": repository_map.get("capabilities", []),
        }
        await get_db().investigations.update_one(
            {"userId": user_id, "sessionId": session["_id"], "runId": run_id},
            {
                "$set": {
                    "userId": user_id,
                    "sessionId": session["_id"],
                    "runId": run_id,
                    "repository": repository_context,
                    "investigation": investigation,
                    "memory": memory_items,
                    "ci": result.get("ci_context"),
                    "timeline": result.get("timeline", []),
                    "deliveryPlan": plan,
                    "toolResult": tool_result,
                    "approvals": [approval_payload] if approval_payload else [],
                    "evidence": [serialize_doc(item) for item in evidence],
                    "createdAt": utc_now(),
                }
            },
            upsert=True,
        )
        await get_db().sessions.update_one({"_id": session["_id"]}, {"$set": {"latestRunId": run_id}})
        if approval_payload:
            yield sse_event("approval.requested", {"approval": serialize_doc(approval_payload)})
            trace.model_usage = result.get("model_usage", {})
            await recorder.complete_run(trace, "completed")
            return

        if persist_messages:
            final = result.get("final", "")
            assistant = await append_message(session["_id"], "assistant", final, sources)
            for chunk in chunk_for_stream(final):
                yield sse_event("message.delta", {"id": assistant["id"], "delta": chunk})
                await asyncio.sleep(0)
            yield sse_event("message.completed", {"message": serialize_doc(assistant)})
        trace.model_usage = result.get("model_usage", {})
        await recorder.complete_run(trace, "completed")
    except HTTPException as exc:
        if trace:
            await TraceRecorder(MongoTraceExporter()).complete_run(trace, "failed", errors=1)
        yield sse_event("error", {"message": str(exc.detail)})
    except Exception as exc:
        if trace:
            await TraceRecorder(MongoTraceExporter()).complete_run(trace, "failed", errors=1)
        # Keep server-side observability useful without serializing provider
        # details (which can include request metadata) into a browser response.
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            logger.error("Agent stream failed: %s (provider HTTP %d)", type(exc).__name__, status_code)
        else:
            logger.error("Agent stream failed: %s", type(exc).__name__)
        yield sse_event(
            "error",
            {
                "message": (
                    "The Safe Repair request could not be completed. Retry once; "
                    "if it persists, check the backend logs for a provider, quota, or connectivity failure."
                )
            },
        )


def chunk_for_stream(text: str, size: int = 80) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


async def update_delivery_commit_message(
    user_id: str,
    slug_id: str,
    plan_id: str,
    payload: CommitMessageUpdateRequest,
) -> dict:
    """Replace a pending approval after the user changes its commit message.

    The exact diff never changes here. The prior approval is invalidated and a
    new one is created with the selected title in canonical arguments, so a
    later push cannot use a message the reviewer did not see.
    """
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    plan = await db.delivery_plans.find_one({"id": plan_id, "userId": user_id, "sessionId": session["_id"]})
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")
    message = payload.commitMessage.strip()
    if not message:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Commit message is required")
    if plan.get("title") == message:
        pending = await db.approvals.find_one({"deliveryPlanId": plan_id, "userId": user_id, "status": "pending"})
        return {"deliveryPlan": serialize_doc(plan), "approval": serialize_doc(pending) if pending else None}

    await db.approvals.update_many(
        {"deliveryPlanId": plan_id, "userId": user_id, "status": "pending"},
        {"$set": {"status": "invalidated", "invalidationReason": "Commit message changed", "updatedAt": utc_now()}},
    )
    await db.delivery_plans.update_one(
        {"id": plan_id, "userId": user_id}, {"$set": {"title": message, "updatedAt": utc_now()}}
    )
    updated = await db.delivery_plans.find_one({"id": plan_id, "userId": user_id})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")
    approval = await DeliveryService().create_approval(DeliveryPlan.model_validate(updated))
    await db.investigations.update_many(
        {"userId": user_id, "sessionId": session["_id"], "deliveryPlan.id": plan_id},
        {"$set": {"deliveryPlan": updated, "approvals": [approval]}},
    )
    return {"deliveryPlan": serialize_doc(updated), "approval": serialize_doc(approval)}


async def request_delivery_approval(user_id: str, slug_id: str, plan_id: str) -> dict:
    """Create a replacement approval for an unchanged, still-current plan.

    This action cannot apply, commit, push, or create a pull request. The
    standard approval decision endpoint remains the only delivery path.
    """

    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    plan = await db.delivery_plans.find_one(
        {"id": plan_id, "userId": user_id, "sessionId": session["_id"]}
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")

    existing = await db.approvals.find_one(
        {
            "deliveryPlanId": plan_id,
            "userId": user_id,
            "sessionId": session["_id"],
            "status": "pending",
        },
        sort=[("createdAt", -1)],
    )
    if existing:
        return {"approval": serialize_doc(existing), "reused": True}

    previous_delivery = await db.delivery_results.find_one(
        {"user_id": user_id, "delivery_plan_id": plan_id}, sort=[("created_at", -1)]
    )
    if previous_delivery and previous_delivery.get("status") == "success":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This exact plan has already been delivered. Generate a new proposal for another change.",
        )

    workspace_service = WorkspaceService()
    repo_path, _repo_name = await workspace_service.ensure_workspace(
        user_id,
        slug_id,
        session["repoUrl"],
        session.get("defaultBranch") or "main",
    )
    if (
        workspace_service.current_branch(repo_path) != plan.get("base_branch")
        or workspace_service.head_commit(repo_path) != plan.get("base_sha")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The repository changed after this proposal was generated. "
                "Generate a new exact proposal before requesting approval."
            ),
        )

    approval = await DeliveryService().create_approval(DeliveryPlan.model_validate(plan))
    await db.investigations.update_many(
        {"userId": user_id, "sessionId": session["_id"], "deliveryPlan.id": plan_id},
        {"$set": {"approvals": [approval]}},
    )
    return {"approval": serialize_doc(approval), "reused": False}


async def decide_approval(user_id: str, slug_id: str, approval_id: str, payload: ApprovalDecisionRequest) -> dict:
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    approval = await db.approvals.find_one({"id": approval_id, "sessionId": session["_id"], "userId": user_id})
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.get("status") != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")
    decision = payload.model_dump()
    if payload.decision == "reject":
        await db.approvals.update_one(
            {"id": approval_id, "userId": user_id, "status": "pending"},
            {
                "$set": {
                    "status": "rejected" if approval.get("deliveryPlanId") else "reject",
                    "decision": decision,
                    "updatedAt": utc_now(),
                }
            },
        )
        message = await append_message(session["_id"], "assistant", "Approval rejected. No action was run.")
        await db.investigations.update_many(
            {"userId": user_id, "sessionId": session["_id"], "runId": approval.get("runId")},
            {"$set": {"approvals": [{**approval, "status": "reject"}], "deliveryResult": {"status": "rejected"}}},
        )
        return {"approval": {**approval, "status": "reject"}, "message": serialize_doc(message)}
    if approval.get("deliveryPlanId"):
        approved = await db.approvals.find_one_and_update(
            {"id": approval_id, "userId": user_id, "status": "pending"},
            {"$set": {"status": "approved", "approved_at": utc_now(), "decision": decision, "updatedAt": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if not approved:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval was already processed")
        repo_path, _repo_name = await WorkspaceService().ensure_workspace(
            user_id, slug_id, session["repoUrl"], session.get("defaultBranch") or "main"
        )
        execution = await DeliveryService().execute(
            user_id=user_id, approval_id=approval_id, repo_path=repo_path, repo_url=session["repoUrl"]
        )
        if execution.get("status") == "success" and execution.get("result", {}).get("id"):
            if get_settings().ci_verification_mode.lower() == "queued":
                job = await BackgroundJobService().enqueue_delivery_ci_verification(
                    user_id=user_id, delivery_result_id=execution["result"]["id"]
                )
                execution["ciVerification"] = {"status": "queued", "jobId": job["id"]}
            else:
                # Minimal-cost mode intentionally has no always-on observer.
                # The Code workbench's bounded commit-status query remains
                # read-only and is still correlated to the delivered SHA.
                execution["ciVerification"] = {
                    "status": "pending",
                    "message": "GitHub Actions status will refresh in the Code commits rail.",
                }
            await db.delivery_results.update_one(
                {"_id": execution["result"]["id"], "user_id": user_id},
                {"$set": {"ciVerification": execution["ciVerification"]}},
            )
        content = (
            (
                "Draft pull request created successfully. GitHub Actions verification was queued."
                if execution.get("ciVerification", {}).get("status") == "queued"
                else (
                    "Draft pull request created successfully. GitHub Actions status will refresh "
                    "in the Code commits rail."
                )
            )
            if execution["status"] == "success"
            else f"Delivery {execution['status']}: {execution.get('reason', 'No mutation was completed.')}"
        )
        message = await append_message(session["_id"], "assistant", content)
        await db.investigations.update_many(
            {"userId": user_id, "sessionId": session["_id"], "runId": approved.get("runId")},
            {"$set": {"approvals": [{**approved, "status": execution["status"]}], "deliveryResult": execution}},
        )
        return {
            "approval": {**approved, "status": execution["status"]},
            "message": serialize_doc(message),
            "result": execution,
        }
    await db.approvals.update_one(
        {"id": approval_id, "userId": user_id},
        {"$set": {"status": payload.decision, "decision": decision, "updatedAt": utc_now()}},
    )
    result = await agent_graph.resume(slug_id, decision)
    final = result.get("final", "Approved action completed.")
    message = await append_message(session["_id"], "assistant", final, result.get("sources", []))
    return {"approval": {**approval, "status": payload.decision}, "message": serialize_doc(message), "result": result}


async def reindex_session(
    user_id: str,
    slug_id: str,
    *,
    repo_url: str | None = None,
    default_branch: str | None = None,
) -> dict:
    """Index an existing session or create a repository-only session first.

    Indexing is a read-only workspace operation. It must not require an
    unrelated agent prompt merely to create the session that owns its evidence.
    A pre-existing session remains bound to its original repository so a browser
    cannot silently swap repositories under an existing slug.
    """
    db = get_db()
    try:
        session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    except PyMongoError as exc:
        return {"success": False, "status": "failed", "indexed": 0, "reason": index_failure_reason(exc)}
    if not session:
        if not repo_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Select a repository before indexing it.",
            )
        session = await get_or_create_session(
            user_id,
            SessionChatRequest(
                slugId=slug_id,
                repoUrl=repo_url,
                defaultBranch=default_branch or "main",
            ),
            "Repository index",
        )
    elif repo_url and repo_url != session.get("repoUrl"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This session is already bound to a different repository.",
        )
    try:
        await db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"indexStatus": "indexing", "indexError": None, "indexTruncated": False}},
        )
    except PyMongoError as exc:
        return {"success": False, "status": "failed", "indexed": 0, "reason": index_failure_reason(exc)}
    if get_settings().indexing_mode.lower() == "queued":
        job = await BackgroundJobService().enqueue_reindex(user_id=user_id, session_id=session["_id"])
        await db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"indexStatus": "queued", "indexTruncated": False, "indexJobId": job["id"]}},
        )
        return {
            "success": True,
            "status": "queued",
            "indexed": 0,
            "jobId": job["id"],
            "reason": "Repository indexing was queued and will continue in the background.",
        }
    try:
        repo_path, _repo_name = await WorkspaceService().ensure_workspace(
            user_id,
            slug_id,
            session["repoUrl"],
            session.get("defaultBranch") or "main",
        )
        result = await RagService().index_workspace(session, repo_path)
        reason = "Repository contains no supported indexable files." if not result.get("indexed") else None
        index_status = "ready" if result.get("indexed") else "empty"
        graph_status = "not_built"
        graph_error = None
        graph_snapshot_id = None
        try:
            snapshot = await EngineeringGraphService().build_and_store(session=session, repo_path=repo_path)
            graph_status = "ready"
            graph_snapshot_id = snapshot.id
        except Exception as exc:
            # Do not turn a successful repository index into a failed request
            # solely because optional graph construction is unavailable.
            graph_status = "failed"
            graph_error = redactor.redact(f"{type(exc).__name__}: {exc}")[:300]
        try:
            await db.sessions.update_one(
                {"_id": session["_id"]},
                {
                    "$set": {
                        "indexStatus": index_status,
                        "indexError": reason,
                        "indexTruncated": bool(result.get("truncated")),
                        "indexedAt": utc_now(),
                        "graphStatus": graph_status,
                        "graphError": graph_error,
                        "graphSnapshotId": graph_snapshot_id,
                    }
                },
            )
        except PyMongoError as exc:
            return {"success": False, "status": "failed", "indexed": 0, "reason": index_failure_reason(exc)}
        return {"success": bool(result.get("indexed")), "status": index_status, "reason": reason, **result}
    except HTTPException as exc:
        reason = str(exc.detail)
    except Exception as exc:
        reason = index_failure_reason(exc)
    try:
        await db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"indexStatus": "failed", "indexError": reason, "indexedAt": utc_now()}},
        )
    except PyMongoError:
        # A lost Atlas connection must not turn an understandable indexing
        # failure into a generic 500 response in the browser.
        pass
    return {"success": False, "status": "failed", "indexed": 0, "reason": reason}


async def list_sources(user_id: str, slug_id: str) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    sources = (
        await get_db()
        .rag_chunks.aggregate(
            [
                {"$match": {"sessionId": session["_id"]}},
                {"$group": {"_id": "$source", "count": {"$sum": 1}, "kind": {"$first": "$kind"}}},
                {"$sort": {"_id": 1}},
            ]
        )
        .to_list(500)
    )
    return {"sources": [{"source": item["_id"], "count": item["count"], "kind": item.get("kind")} for item in sources]}


async def list_evidence(user_id: str, slug_id: str) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    evidence = await EvidenceService().list_for_session(user_id, session["_id"])
    return {"evidence": [serialize_doc(item) for item in evidence]}


async def refresh_latest_ci(user_id: str, slug_id: str) -> dict:
    """Bring the newest failed Actions run into the session's read-only CI view.

    This is intentionally an explicit server-side read operation: it uses the
    repository bound to the authenticated session, never a browser-supplied
    owner/name or run ID. It persists only normal, redacted evidence produced
    by the existing CI investigation service.
    """
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, repo_name = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch") or "main"
    )
    resolved = await CIRunResolutionService().resolve(
        user_id=user_id,
        repository_id=session["repoUrl"],
        intent=extract_ci_request_intent("latest failed CI run"),
    )
    if not resolved:
        await db.sessions.update_one({"_id": session["_id"]}, {"$set": {"ciLastCheckedAt": utc_now()}})
        return {
            "status": "no_failed_runs",
            "message": "No failed GitHub Actions run was available for this repository.",
        }

    graph_run_id = new_id("run_")
    context = await CIInvestigationService(workspace=workspace).investigate_actions_run(
        user_id=user_id,
        repository_id=session["repoUrl"],
        run_id=resolved.run_id,
        session={
            "_id": session["_id"],
            "repoUrl": session["repoUrl"],
            "defaultBranch": session.get("defaultBranch"),
        },
        graph_run_id=graph_run_id,
        current_head_sha=workspace.head_commit(repo_path),
        repo_path=repo_path,
    )
    evidence_ids = context.evidence_ids + context.workflow_evidence_ids + context.changed_file_evidence_ids
    evidence = await db.evidence.find({"id": {"$in": evidence_ids}}).to_list(len(evidence_ids) or 1)
    repository_map = workspace.repository_map(repo_path)
    timeline = [
        {
            "id": new_id("evt_"),
            "label": "GitHub Actions refreshed",
            "detail": f"{resolved.workflow_name or 'Workflow'} run #{resolved.run_id}; "
            f"{len(context.failed_jobs)} failed job(s)",
            "status": "partial" if context.incomplete else "completed",
        }
    ]
    await db.investigations.update_one(
        {"userId": user_id, "sessionId": session["_id"], "runId": graph_run_id},
        {
            "$set": {
                "userId": user_id,
                "sessionId": session["_id"],
                "runId": graph_run_id,
                "repository": {
                    "name": repo_name,
                    "branch": session.get("defaultBranch") or "main",
                    "commit_sha": workspace.head_commit(repo_path),
                    "file_count": repository_map["fileCount"],
                    "ci_files": repository_map["ci"],
                    "manifests": repository_map["manifests"],
                },
                "investigation": None,
                "memory": [],
                "ci": context.model_dump(mode="json"),
                "timeline": timeline,
                "deliveryPlan": None,
                "toolResult": None,
                "approvals": [],
                "evidence": [serialize_doc(item) for item in evidence],
                "createdAt": utc_now(),
            }
        },
        upsert=True,
    )
    await db.sessions.update_one(
        {"_id": session["_id"]},
        {"$set": {"latestRunId": graph_run_id, "ciLastCheckedAt": utc_now()}},
    )
    return {
        "status": "found",
        "runId": resolved.run_id,
        "workflowName": resolved.workflow_name,
        "failedJobs": len(context.failed_jobs),
        "incomplete": context.incomplete,
        "limitations": context.limitations,
    }


def _public_delivery_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return review metadata/diffs, never persisted source or execution bytes."""

    if not plan:
        return None
    public_plan = serialize_doc(plan) or {}
    public_files = []
    for item in _dict_items(public_plan.get("files")):
        unified_diff, diff_truncated = _bounded_replay_text(
            item.get("unified_diff"), _MAX_REPLAY_DIFF_BYTES
        )
        public_files.append(
            {
                "path": _safe_replay_text(item.get("path")),
                "change_type": _safe_replay_text(item.get("change_type")),
                "original_hash": _safe_replay_text(item.get("original_hash")),
                "proposed_hash": _safe_replay_text(item.get("proposed_hash")),
                "unified_diff": unified_diff,
                "diff_truncated": diff_truncated,
            }
        )
    public_validation = [
        {
            "id": _safe_replay_text(item.get("id")),
            "kind": _safe_replay_text(item.get("kind")),
            "description": _safe_replay_text(item.get("description")),
            "required": item.get("required") if isinstance(item.get("required"), bool) else True,
        }
        for item in _dict_items(public_plan.get("validation_steps"))
    ]
    return {
        "id": _safe_replay_text(public_plan.get("id")),
        "title": _safe_replay_text(public_plan.get("title")),
        "rationale": _safe_replay_text(public_plan.get("rationale")),
        "risk_level": _safe_replay_text(public_plan.get("risk_level")),
        "risk_reasons": _string_items(public_plan.get("risk_reasons")),
        "evidence_ids": _string_items(public_plan.get("evidence_ids")),
        "files": public_files,
        "validation_steps": public_validation,
    }


def _public_trace(trace: dict[str, Any] | None) -> dict[str, Any] | None:
    """Expose only operational telemetry, not tenant/session identifiers or metadata."""

    if not trace:
        return None
    value = serialize_doc(trace) or {}
    return {
        "id": _safe_replay_text(value.get("id")),
        "status": _safe_replay_text(value.get("status")),
        "started_at": _safe_replay_text(value.get("started_at")),
        "completed_at": _safe_replay_text(value.get("completed_at")),
        "total_duration_ms": _safe_replay_int(value.get("total_duration_ms")),
        "model_usage": _public_model_usage(value.get("model_usage")),
        "error_count": _safe_replay_int(value.get("error_count")) or 0,
    }


# Usage is observability data, not a general-purpose trace metadata channel.
# An explicit allowlist prevents a legacy trace from accidentally surfacing
# provider payloads, prompts, headers, or arbitrary diagnostic annotations in
# the browser replay. These are the fields Base64 itself records in AgentGraph.
_REPLAY_USAGE_TEXT_FIELDS = frozenset({"model", "provider_error"})
_REPLAY_USAGE_NUMBER_FIELDS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_input_tokens",
        "context_chars",
        "retrieved_chunks",
        "kept_chunks",
        "discarded_chunks",
        "cost",
    }
)
_REPLAY_USAGE_BOOLEAN_FIELDS = frozenset({"context_truncated"})


def _public_model_usage(value: Any) -> dict[str, Any]:
    """Return bounded, typed model-use telemetry for a historical replay.

    Persisted traces can outlive the schema that created them. Never forward
    arbitrary legacy ``model_usage`` keys merely because the record is owned
    by the tenant; replay should show counts and model identity only.
    """

    usage = _safe_replay_dict(value)
    public: dict[str, Any] = {}
    for key in _REPLAY_USAGE_TEXT_FIELDS:
        item = _safe_replay_text(usage.get(key))
        if item:
            public[key] = item[:200]
    for key in _REPLAY_USAGE_NUMBER_FIELDS:
        item = usage.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
            public[key] = item
    for key in _REPLAY_USAGE_BOOLEAN_FIELDS:
        item = usage.get(key)
        if isinstance(item, bool):
            public[key] = item
    return public


def _public_span(span: dict[str, Any]) -> dict[str, Any]:
    """Return the explicit safe span surface; trace metadata stays server-side."""

    value = serialize_doc(span) or {}
    return {
        "id": _safe_replay_text(value.get("id")),
        "parent_span_id": _safe_replay_text(value.get("parent_span_id")),
        "name": _safe_replay_text(value.get("name")),
        "started_at": _safe_replay_text(value.get("started_at")),
        "completed_at": _safe_replay_text(value.get("completed_at")),
        "duration_ms": _safe_replay_int(value.get("duration_ms")),
        "status": _safe_replay_text(value.get("status")),
        "safe_input_summary": _safe_replay_dict(value.get("safe_input_summary")),
        "safe_output_summary": _safe_replay_dict(value.get("safe_output_summary")),
        "error_type": _safe_replay_text(value.get("error_type")),
        "error_message": _safe_replay_text(value.get("error_message")),
    }


def _public_approval(approval: dict[str, Any] | None) -> dict[str, Any] | None:
    """Show approval status/binding metadata, never the canonical execution arguments."""

    if not approval:
        return None
    value = serialize_doc(approval) or {}
    return {
        "id": _safe_replay_text(value.get("id")),
        "status": _safe_replay_text(value.get("status")),
        "risk_level": _safe_replay_text(value.get("risk_level") or value.get("riskLevel")),
        "diff_hash": _safe_replay_text(value.get("diff_hash") or value.get("diffHash")),
        "created_at": _safe_replay_text(value.get("created_at") or value.get("createdAt")),
        "approved_at": _safe_replay_text(value.get("approved_at") or value.get("approvedAt")),
        "executed_at": _safe_replay_text(value.get("executed_at") or value.get("executedAt")),
    }


def _public_delivery_result(delivery: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the display-safe delivery outcome and redacted validation summaries."""

    if not delivery:
        return None
    value = serialize_doc(delivery) or {}
    validation = [
        {
            "step_id": _safe_replay_text(item.get("step_id")),
            "status": _safe_replay_text(item.get("status")),
            "summary": redactor.redact(_safe_replay_text(item.get("summary")) or "")[:1_000],
            "duration_ms": _safe_replay_int(item.get("duration_ms")),
            "evidence_id": _safe_replay_text(item.get("evidence_id")),
        }
        for item in _dict_items(value.get("validation_results"))
    ]
    return {
        "id": _safe_replay_text(value.get("id")),
        "status": _safe_replay_text(value.get("status")),
        "failure_stage": _safe_replay_text(value.get("failure_stage")),
        "branch_name": _safe_replay_text(value.get("branch_name")),
        "commit_sha": _safe_replay_text(value.get("commit_sha")),
        "pull_request_number": _safe_replay_int(value.get("pull_request_number")),
        "pull_request_url": _safe_replay_text(value.get("pull_request_url")),
        "validation_results": validation,
        "created_at": _safe_replay_text(value.get("created_at")),
    }


def _public_evidence(item: dict[str, Any]) -> dict[str, Any]:
    """Keep inspector evidence compact; excerpts remain available in the normal evidence UI."""

    serialized = serialize_doc(item) or {}
    metadata = serialized.get("metadata")
    safe_metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "id": _safe_replay_text(serialized.get("id")),
        "path": _safe_replay_text(serialized.get("path") or serialized.get("source")) or "Unknown source",
        "source": _safe_replay_text(serialized.get("title") or serialized.get("source")),
        "source_type": _safe_replay_text(serialized.get("sourceType") or serialized.get("source_type")) or "repo_file",
        "retrieval_method": _safe_replay_text(serialized.get("retrievalMethod") or serialized.get("retrieval_method"))
        or "evidence",
        "score": _safe_replay_float(serialized.get("retrievalScore", serialized.get("score"))),
        "commit_sha": _safe_replay_text(serialized.get("commitSha") or serialized.get("commit_sha")),
        "state": _safe_replay_text(safe_metadata.get("state")),
        "truncated": bool(safe_metadata.get("truncated", False)),
    }


def _public_replay_impact_target(value: Any) -> dict[str, Any] | None:
    """Remove graph-internal identities while retaining a concise explanation."""

    target = _safe_replay_dict(value)
    if not target:
        return None
    relationship_path = _dict_items(target.get("relationship_path"))[:8]
    distance = _safe_replay_int(target.get("distance"))
    return {
        "node_type": _safe_replay_text(target.get("node_type")),
        "name": _safe_replay_text(target.get("name")),
        "path": _safe_replay_text(target.get("path")),
        "qualified_name": _safe_replay_text(target.get("qualified_name")),
        "distance": distance if distance and distance > 0 else None,
        "relations": [
            relation
            for item in relationship_path
            if (relation := _safe_replay_text(item.get("relation_type"))) is not None
        ],
        "explanation": _safe_replay_text(target.get("explanation")),
    }


def _public_change_impact(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a strictly allowlisted historical impact report for replay."""

    if not value:
        return None
    impact = serialize_doc(value) or {}
    status_value = _safe_replay_text(impact.get("status"))
    status_value = status_value if status_value in {"available", "unavailable"} else None

    def targets(key: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in _dict_items(impact.get(key))[:100]:
            safe_target = _public_replay_impact_target(item)
            if safe_target:
                result.append(safe_target)
        return result

    return {
        "id": _safe_replay_text(impact.get("id")),
        "status": status_value,
        "plan_base_sha": _safe_replay_text(impact.get("planBaseSha") or impact.get("plan_base_sha")),
        "graph_commit_sha": _safe_replay_text(impact.get("graphCommitSha") or impact.get("graph_commit_sha")),
        "changed_paths": _string_items(impact.get("changedPaths") or impact.get("changed_paths"))[:100],
        "unmapped_paths": _string_items(impact.get("unmappedPaths") or impact.get("unmapped_paths"))[:100],
        "directly_affected": targets("directlyAffected") or targets("directly_affected"),
        "indirectly_affected": targets("indirectlyAffected") or targets("indirectly_affected"),
        "related_tests": targets("relatedTests") or targets("related_tests"),
        "related_workflows": targets("relatedWorkflows") or targets("related_workflows"),
        "truncated": bool(impact.get("truncated")),
        "limitations": _string_items(impact.get("limitations"))[:50],
        "created_at": _safe_replay_timestamp(impact.get("createdAt") or impact.get("created_at")),
    }


def _public_replay_repository(repository: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize historical repository provenance to the replay allowlist."""

    value = serialize_doc(repository or {}) or {}
    return {
        "headSha": _safe_replay_text(value.get("headSha")),
        "head_sha": _safe_replay_text(value.get("head_sha") or value.get("commit_sha")),
        "branch": _safe_replay_text(value.get("branch")),
    }


def _public_ci_investigation(ci: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize legacy CI context before final replay schema validation."""

    if not ci:
        return None
    value = serialize_doc(ci) or {}
    return {
        "run_id": _safe_replay_int(value.get("run_id")),
        "workflow_name": _safe_replay_text(value.get("workflow_name")),
        "head_sha": _safe_replay_text(value.get("head_sha")),
        "current_head_sha": _safe_replay_text(value.get("current_head_sha")),
        "run_is_historical": value.get("run_is_historical")
        if isinstance(value.get("run_is_historical"), bool)
        else None,
        "applicability": _safe_replay_text(value.get("applicability")),
        "applicability_reason": _safe_replay_text(value.get("applicability_reason")),
        "failed_jobs": [
            {
                "name": _safe_replay_text(item.get("name")),
                "failed_step": _safe_replay_text(item.get("failed_step")),
            }
            for item in _dict_items(value.get("failed_jobs"))
        ],
        "evidence_ids": _string_items(value.get("evidence_ids")),
        "workflow_evidence_ids": _string_items(value.get("workflow_evidence_ids")),
        "changed_file_evidence_ids": _string_items(value.get("changed_file_evidence_ids")),
        "relevant_paths": _string_items(value.get("relevant_paths")),
        "limitations": _string_items(value.get("limitations")),
    }


def _public_replay_investigation(investigation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not investigation:
        return None
    value = serialize_doc(investigation) or {}
    return {
        "summary": _safe_replay_text(value.get("summary")),
        "diagnosis": _safe_replay_text(value.get("diagnosis")),
        "status": _safe_replay_text(value.get("status")),
    }


def _public_replay_timeline(value: Any) -> list[dict[str, Any]]:
    """Normalize partial legacy timeline records to the visible event contract."""

    return [
        {
            "label": _safe_replay_text(item.get("label") or item.get("name")),
            "detail": _safe_replay_text(item.get("detail")),
            "status": _safe_replay_text(item.get("status")),
            "timestamp": _safe_replay_text(item.get("timestamp") or item.get("createdAt")),
        }
        for item in _dict_items(value)
    ]


def _redact_replay_value(value: Any) -> Any:
    """Apply the central redactor to every browser-bound replay string.

    Current records are redacted before persistence, but replay intentionally
    supports older snapshots as well.  A final pass makes that compatibility
    path safe without trusting an historical document to have used every
    modern write boundary.  Keys are schema-allowlisted separately; only
    values need redaction here.
    """

    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, list):
        return [_redact_replay_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_replay_value(item) for key, item in value.items()}
    return value


def _finalize_replay(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the same strict public contract used by both replay routes."""

    redacted = _redact_replay_value(payload)
    return RunReplayResponse.model_validate(redacted).model_dump(mode="json")


def _finalize_run_history(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the replay's redaction/allowlist boundary to run-picker metadata."""

    redacted = _redact_replay_value(payload)
    return SessionRunHistoryResponse.model_validate(redacted).model_dump(mode="json")


async def _rebuild_run_replay(
    *, user_id: str, session: dict[str, Any], run_id: str, trace_id: str | None = None
) -> dict[str, Any]:
    """Read a persisted run snapshot without invoking the graph, GitHub, or a mutation path."""

    db = get_db()
    session_id = session["_id"]
    snapshot = await db.investigations.find_one({"userId": user_id, "sessionId": session_id, "runId": run_id})
    trace_query: dict[str, Any] = {"user_id": user_id, "session_id": session_id, "run_id": run_id}
    if trace_id:
        trace_query = {"user_id": user_id, "session_id": session_id, "id": trace_id}
    trace = await db.run_traces.find_one(trace_query)
    if not snapshot and not trace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    spans: list[dict[str, Any]] = []
    trace_id = _safe_replay_text(trace.get("id")) if isinstance(trace, dict) else None
    if trace_id:
        spans = await db.trace_spans.find({"trace_id": trace_id}).sort("started_at", 1).to_list(500)

    evidence = await (
        db.evidence.find(
            {"userId": user_id, "sessionId": session_id, "runId": run_id},
            _REPLAY_EVIDENCE_PROJECTION,
        )
        .sort("retrievedAt", 1)
        .to_list(200)
    )
    snapshot_plan = snapshot.get("deliveryPlan") if snapshot else None
    plan_id = (snapshot_plan or {}).get("id") if isinstance(snapshot_plan, dict) else None
    plan = None
    if plan_id:
        plan = await db.delivery_plans.find_one(
            {"id": plan_id, "user_id": user_id, "session_id": session_id, "run_id": run_id}
        )
    if not plan:
        plan = await db.delivery_plans.find_one({"user_id": user_id, "session_id": session_id, "run_id": run_id})
    if not plan:
        plan = snapshot_plan

    resolved_plan_id = (plan or {}).get("id") if isinstance(plan, dict) else None
    impact = None
    impact_collection = getattr(db, "change_impact_analyses", None)
    if resolved_plan_id and impact_collection is not None:
        impact = await impact_collection.find_one(
            {"userId": user_id, "sessionId": session_id, "deliveryPlanId": resolved_plan_id},
            sort=[("createdAt", -1)],
        )

    approval = await db.approvals.find_one(
        {"user_id": user_id, "session_id": session_id, "run_id": run_id},
        sort=[("created_at", -1)],
    )
    if not approval and isinstance(snapshot, dict):
        approvals = snapshot.get("approvals") or []
        approval = approvals[-1] if approvals and isinstance(approvals[-1], dict) else None

    delivery = None
    if resolved_plan_id:
        delivery = await db.delivery_results.find_one(
            {"user_id": user_id, "delivery_plan_id": resolved_plan_id},
            sort=[("created_at", -1)],
        )
    if not delivery and snapshot:
        delivery = snapshot.get("deliveryResult")

    limitations: list[str] = []
    if not trace:
        limitations.append(
            "Trace telemetry was not recorded for this historical run; persisted investigation data is shown."
        )
    elif not trace_id:
        limitations.append(
            "Trace span telemetry was unavailable because this historical trace has no identifier."
        )
    if not snapshot:
        limitations.append(
            "A persisted investigation snapshot was not recorded for this run; only available trace telemetry is shown."
        )
    if snapshot and not evidence and _dict_items(snapshot.get("evidence")):
        # Older snapshots stored the exact selected evidence inline. It remains
        # redacted and is a safe compatibility fallback.
        evidence = _dict_items(snapshot.get("evidence"))[:200]
        limitations.append("Evidence is replayed from the historical snapshot because indexed records are unavailable.")
    if snapshot and isinstance(snapshot.get("ci"), dict):
        limitations.extend(_string_items(snapshot["ci"].get("limitations")))

    repository = snapshot.get("repository", {}) if snapshot else {}
    ci_context = snapshot.get("ci") if snapshot else None
    return _finalize_replay(
        {
            "run_id": run_id,
            "historical": True,
            "replay_mode": "read_only",
            "session": {
                "slug_id": _safe_replay_text(session.get("slugId")),
                "repository": _safe_replay_text(session.get("repoName")),
            },
            "repository": _public_replay_repository(repository if isinstance(repository, dict) else None),
            "investigation": _public_replay_investigation(
                snapshot.get("investigation") if snapshot and isinstance(snapshot.get("investigation"), dict) else None
            ),
            "timeline": _public_replay_timeline(snapshot.get("timeline") if snapshot else None),
            "trace": _public_trace(trace),
            "spans": [_public_span(item) for item in _dict_items(spans)],
            "delivery_plan": _public_delivery_plan(plan if isinstance(plan, dict) else None),
            "approval": _public_approval(approval if isinstance(approval, dict) else None),
            "delivery_result": _public_delivery_result(delivery if isinstance(delivery, dict) else None),
            "ci_investigation": _public_ci_investigation(ci_context if isinstance(ci_context, dict) else None),
            "tool_result": safe_tool_result(snapshot.get("toolResult")) if snapshot else None,
            "evidence_count": len(evidence),
            "evidence": [_public_evidence(item) for item in evidence],
            "change_impact": _public_change_impact(impact if isinstance(impact, dict) else None),
            "limitations": list(dict.fromkeys(limitations)),
        }
    )


async def get_run_replay(user_id: str, slug_id: str, run_id: str) -> dict[str, Any]:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return await _rebuild_run_replay(user_id=user_id, session=session, run_id=run_id)


async def get_run_replay_by_id(user_id: str, run_reference: str) -> dict[str, Any]:
    """Resolve the developer inspector route without crossing a tenant/session boundary."""

    db = get_db()
    snapshot = await db.investigations.find_one({"userId": user_id, "runId": run_reference})
    trace = None
    if not snapshot:
        trace = await db.run_traces.find_one(
            {"user_id": user_id, "$or": [{"run_id": run_reference}, {"id": run_reference}]}
        )
    session_id = snapshot.get("sessionId") if snapshot else trace.get("session_id") if trace else None
    resolved_run_id = snapshot.get("runId") if snapshot else trace.get("run_id") if trace else None
    if not session_id or not resolved_run_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    session = await db.sessions.find_one({"_id": session_id, "userId": user_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return await _rebuild_run_replay(
        user_id=user_id,
        session=session,
        run_id=resolved_run_id,
        trace_id=trace.get("id") if trace and trace.get("id") == run_reference else None,
    )


async def list_session_runs(user_id: str, slug_id: str, limit: int = 50) -> dict[str, Any]:
    """List replayable historical runs without loading evidence bodies or re-executing work."""

    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    session_id = session["_id"]
    bounded_limit = min(max(limit, 1), 100)
    snapshots = await (
        db.investigations.find(
            {"userId": user_id, "sessionId": session_id},
            {
                "_id": 0,
                "runId": 1,
                "createdAt": 1,
                "investigation.summary": 1,
                "investigation.status": 1,
                "ci.run_id": 1,
                "ci.workflow_name": 1,
                "deliveryPlan.id": 1,
                "deliveryPlan.title": 1,
                "deliveryResult.status": 1,
                "evidence.id": 1,
            },
        )
        .sort("createdAt", -1)
        .limit(bounded_limit)
        .to_list(bounded_limit)
    )
    # Preserve the run picker as a bounded feed, but do not rely on temporal
    # ordering to join a selected snapshot with its trace. Older imports and
    # delayed trace persistence can make a relevant trace fall outside the
    # most-recent trace window even though its investigation is displayed.
    recent_traces = await (
        db.run_traces.find(
            {"user_id": user_id, "session_id": session_id},
            {
                "_id": 0,
                "id": 1,
                "run_id": 1,
                "status": 1,
                "started_at": 1,
                "total_duration_ms": 1,
                "error_count": 1,
            },
        )
        .sort("started_at", -1)
        .limit(bounded_limit)
        .to_list(bounded_limit)
    )
    snapshot_run_ids = [
        run_id
        for snapshot in snapshots
        if (run_id := _safe_replay_text(snapshot.get("runId"))) is not None
    ]
    snapshot_traces: list[dict[str, Any]] = []
    if snapshot_run_ids:
        snapshot_traces = await (
            db.run_traces.find(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "run_id": {"$in": snapshot_run_ids},
                },
                {
                    "_id": 0,
                    "id": 1,
                    "run_id": 1,
                    "status": 1,
                    "started_at": 1,
                    "total_duration_ms": 1,
                    "error_count": 1,
                },
            )
            .to_list(len(snapshot_run_ids))
        )
    plan_ids = [
        str(snapshot.get("deliveryPlan", {}).get("id"))
        for snapshot in snapshots
        if isinstance(snapshot.get("deliveryPlan"), dict) and snapshot["deliveryPlan"].get("id")
    ]
    delivery_by_plan: dict[str, dict[str, Any]] = {}
    if plan_ids:
        delivery_results = await (
            db.delivery_results.find(
                {"user_id": user_id, "delivery_plan_id": {"$in": plan_ids}},
                {"_id": 0, "delivery_plan_id": 1, "status": 1},
            )
            .sort("created_at", -1)
            .to_list(len(plan_ids))
        )
        # Results are newest-first, so the first one is authoritative when a
        # read-only CI observer later writes a follow-up status update.
        for result in delivery_results:
            delivery_by_plan.setdefault(str(result.get("delivery_plan_id")), result)
    impact_plan_ids: set[str] = set()
    impact_collection = getattr(db, "change_impact_analyses", None)
    if plan_ids and impact_collection is not None:
        impact_records = await (
            impact_collection.find(
                {
                    "userId": user_id,
                    "sessionId": session_id,
                    "deliveryPlanId": {"$in": plan_ids},
                },
                {"_id": 0, "deliveryPlanId": 1},
            )
            .to_list(len(plan_ids))
        )
        impact_plan_ids = {
            plan_id
            for record in impact_records
            if (plan_id := _safe_replay_text(record.get("deliveryPlanId"))) is not None
        }
    # The exact snapshot join wins over the feed query when both contain a
    # trace, ensuring an old but visible run never loses its telemetry.
    trace_by_run = {
        run_id: item
        for item in [*recent_traces, *snapshot_traces]
        if (run_id := _safe_replay_text(item.get("run_id"))) is not None
    }
    records: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        run_id = _safe_replay_text(snapshot.get("runId"))
        if not run_id:
            continue
        trace = trace_by_run.pop(run_id, None)
        investigation = _safe_replay_dict(snapshot.get("investigation"))
        ci = _safe_replay_dict(snapshot.get("ci"))
        delivery_plan = _safe_replay_dict(snapshot.get("deliveryPlan"))
        delivery_result = _safe_replay_dict(
            delivery_by_plan.get(str(delivery_plan.get("id"))) or snapshot.get("deliveryResult")
        )
        title = (
            _safe_replay_text(delivery_plan.get("title"))
            or _safe_replay_text(investigation.get("summary"))
            or _safe_replay_text(ci.get("workflow_name"))
            or "Historical investigation"
        )
        records[run_id] = {
            "run_id": run_id,
            "trace_id": _safe_replay_text(trace.get("id")) if trace else None,
            "created_at": _safe_replay_timestamp(
                snapshot.get("createdAt") or (trace.get("started_at") if trace else None)
            ),
            "status": (
                _safe_replay_text(delivery_result.get("status"))
                or (_safe_replay_text(trace.get("status")) if trace else None)
                or _safe_replay_text(investigation.get("status"))
                or "completed"
            ),
            "title": title[:240],
            "kind": "ci" if ci else "delivery" if delivery_plan else "investigation",
            "evidence_count": len(_dict_items(snapshot.get("evidence"))),
            "has_trace": bool(trace),
            "ci_run_id": _safe_replay_int(ci.get("run_id")),
            "delivery_status": _safe_replay_text(delivery_result.get("status")),
            "has_change_impact": str(delivery_plan.get("id")) in impact_plan_ids,
        }
    for run_id, trace in trace_by_run.items():
        records[run_id] = {
            "run_id": run_id,
            "trace_id": _safe_replay_text(trace.get("id")),
            "created_at": _safe_replay_timestamp(trace.get("started_at")),
            "status": _safe_replay_text(trace.get("status")) or "completed",
            "title": "Historical agent run",
            "kind": "investigation",
            "evidence_count": 0,
            "has_trace": True,
            "ci_run_id": None,
            "delivery_status": None,
            "has_change_impact": False,
        }
    runs = list(records.values())
    runs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return _finalize_run_history({"runs": runs[:bounded_limit], "limit": bounded_limit})


async def create_runbook(user_id: str, title: str, content: str, tags: list[str]) -> dict:
    runbook = await RagService().add_runbook(user_id, title, content, tags)
    return {"runbook": serialize_doc(runbook)}


async def list_runbooks(user_id: str) -> dict:
    runbooks = await RagService().list_runbooks(user_id)
    return {"runbooks": [serialize_doc(runbook) for runbook in runbooks]}


def repo_owner_name(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub repository URL")
    return parts[-2], parts[-1]


async def create_pull_request(user_id: str, slug_id: str, _payload: CreatePullRequestRequest) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    # The former implementation accepted a legacy, unbound approval record and
    # then pushed a branch and created a PR.  It did not verify the immutable
    # delivery-plan hash, base SHA, branch, or canonical arguments that the
    # current approval flow binds.  Keeping this compatibility route capable of
    # mutation would make an old approval an authorization bypass.
    #
    # Delivery is therefore available only through `decide_approval`, which
    # invokes DeliveryService.execute after validating the exact approved plan.
    # Keep this route fail-closed so older clients cannot accidentally create a
    # pull request with a stale or unbound approval.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "The legacy pull-request endpoint is disabled. Approve the exact "
            "delivery plan through the approval endpoint to create a draft pull request."
        ),
    )
