import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.agent.graph import AgentGraph
from app.db.mongo import get_db
from app.git.actions import GitHubActionsAdapter
from app.observability.recorder import MongoTraceExporter, TraceRecorder
from app.schemas.delivery import DeliveryPlan
from app.schemas.patch import FileEditIntent, PatchProposal
from app.schemas.session import (
    ApprovalDecisionRequest,
    CodeEditProposalRequest,
    CommitMessageUpdateRequest,
    CreatePullRequestRequest,
    SessionChatRequest,
)
from app.services.ci_investigation_service import CIInvestigationService
from app.services.ci_request_intent import extract_ci_request_intent
from app.services.ci_run_resolution_service import CIRunResolutionService
from app.services.delivery_service import DeliveryService, content_hash
from app.services.evidence_service import EvidenceService
from app.services.github_service import github_api
from app.services.patch_engine import PatchEngine, PatchSafetyError
from app.services.rag_service import RagService
from app.services.redaction_service import RedactionService
from app.services.serializers import serialize_doc
from app.services.workspace_service import WorkspaceService, branch_name_from_prompt, repo_name_from_url
from app.utils.datetime import iso_now, utc_now
from app.utils.ids import new_id
from app.utils.sse import sse_event

agent_graph = AgentGraph()
redactor = RedactionService()
CI_REFRESH_INTERVAL = timedelta(minutes=1)


def ci_refresh_due(value: Any) -> bool:
    """Throttle automatic Actions discovery without making it one-shot."""
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
    return datetime.now(UTC) - checked_at >= CI_REFRESH_INTERVAL


def safe_tool_result(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep a compact, redacted diagnostic outcome safe for SSE and persistence."""
    if not value:
        return None
    return {
        "success": bool(value.get("success")),
        "output": redactor.redact(str(value.get("output", "")))[:4_000],
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
    repo_name = repo_name_from_url(payload.repoUrl)
    now = utc_now()
    session = {
        "_id": new_id("ses_"),
        "userId": user_id,
        "slugId": payload.slugId,
        "title": prompt[:48] or "Untitled Session",
        "repoUrl": payload.repoUrl,
        "repoName": repo_name,
        "defaultBranch": payload.defaultBranch or "main",
        "branchName": branch_name_from_prompt(prompt),
        "status": "active",
        "repoInitializedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
    await db.sessions.insert_one(session)
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
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"repoName": {"$regex": search, "$options": "i"}},
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
    # Surface GitHub Actions failures without asking the user to paste a log.
    # This remains a bounded read-only refresh and is best-effort: an OAuth or
    # Actions-permission problem must not make the session itself unavailable.
    if session.get("repoUrl") and ci_refresh_due(session.get("ciLastCheckedAt")):
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
        command_center = {
            "runId": latest.get("runId"),
            "repository": latest.get("repository", {}),
            "investigation": latest.get("investigation"),
            "memory": latest.get("memory", []),
            "ci": latest.get("ci"),
            "timeline": latest.get("timeline", []),
            "deliveryPlan": latest.get("deliveryPlan"),
            "deliveryResult": latest.get("deliveryResult"),
            "toolResult": latest.get("toolResult"),
            "approvals": latest.get("approvals", []),
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
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch")
    )
    files = []
    for path in workspace.iter_indexable_files(repo_path):
        files.append({
            "path": str(path.relative_to(repo_path)).replace("\\", "/"),
            "bytes": path.stat().st_size,
        })
    return {"headSha": workspace.head_commit(repo_path), "files": files}


async def get_session_git_status(user_id: str, slug_id: str) -> dict:
    """Return git status --short output for the session workspace (read-only)."""
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch")
    )
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
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch")
    )
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
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch")
    )
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
            is_newer = existing is not None and run.updated_at and (
                not existing.updated_at or run.updated_at > existing.updated_at
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
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch")
    )
    return workspace.dependency_graph(repo_path)


async def read_session_code_file(user_id: str, slug_id: str, path: str) -> dict:
    """Read one safe repository file through the existing workspace boundary.

    This endpoint deliberately has no write counterpart. Content is bounded and sent through
    centralized redaction before it reaches the browser.
    """
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    workspace = WorkspaceService()
    repo_path, _ = await workspace.ensure_workspace(
        user_id, slug_id, session["repoUrl"], session.get("defaultBranch")
    )
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
                "approvals": [{
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
                }],
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


async def chat_stream(user_id: str, payload: SessionChatRequest) -> AsyncIterator[str]:
    prompt = extract_prompt(payload).strip()
    if not prompt:
        yield sse_event("error", {"message": "Message is required"})
        return
    trace = None
    try:
        session = await get_or_create_session(user_id, payload, prompt)
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
            span.safe_output_summary = {"timeline_events": len(result.get("timeline", []))}

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
            {"$set": {
                "userId": user_id, "sessionId": session["_id"], "runId": run_id,
                "repository": repository_context, "investigation": investigation,
                "memory": memory_items, "ci": result.get("ci_context"),
                "timeline": result.get("timeline", []), "deliveryPlan": plan,
                "toolResult": tool_result,
                "approvals": [approval_payload] if approval_payload else [],
                "evidence": [serialize_doc(item) for item in evidence], "createdAt": utc_now(),
            }}, upsert=True,
        )
        await get_db().sessions.update_one({"_id": session["_id"]}, {"$set": {"latestRunId": run_id}})
        if approval_payload:
            yield sse_event("approval.requested", {"approval": serialize_doc(approval_payload)})
            await recorder.complete_run(trace, "completed")
            return

        final = result.get("final", "")
        assistant = await append_message(session["_id"], "assistant", final, sources)
        for chunk in chunk_for_stream(final):
            yield sse_event("message.delta", {"id": assistant["id"], "delta": chunk})
            await asyncio.sleep(0)
        yield sse_event("message.completed", {"message": serialize_doc(assistant)})
        await recorder.complete_run(trace, "completed")
    except HTTPException as exc:
        if trace:
            await TraceRecorder(MongoTraceExporter()).complete_run(trace, "failed", errors=1)
        yield sse_event("error", {"message": str(exc.detail)})
    except Exception:
        if trace:
            await TraceRecorder(MongoTraceExporter()).complete_run(trace, "failed", errors=1)
        yield sse_event(
            "error",
            {"message": ("The agent could not complete this request. Check the API configuration and try again.")},
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
    plan = await db.delivery_plans.find_one(
        {"id": plan_id, "userId": user_id, "sessionId": session["_id"]}
    )
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")
    message = payload.commitMessage.strip()
    if not message:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Commit message is required")
    if plan.get("title") == message:
        pending = await db.approvals.find_one(
            {"deliveryPlanId": plan_id, "userId": user_id, "status": "pending"}
        )
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
        content = (
            "Draft pull request created successfully."
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
            {"$set": {"indexStatus": "indexing", "indexError": None}},
        )
    except PyMongoError as exc:
        return {"success": False, "status": "failed", "indexed": 0, "reason": index_failure_reason(exc)}
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
        try:
            await db.sessions.update_one(
                {"_id": session["_id"]},
                {"$set": {"indexStatus": index_status, "indexError": reason, "indexedAt": utc_now()}},
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


async def get_run_replay(user_id: str, slug_id: str, run_id: str) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    trace = await get_db().run_traces.find_one({"user_id": user_id, "session_id": session["_id"], "run_id": run_id})
    if not trace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run trace not found")
    spans = await get_db().trace_spans.find({"trace_id": trace["id"]}).sort("started_at", 1).to_list(500)
    evidence = (
        await get_db().evidence.find({"userId": user_id, "sessionId": session["_id"], "runId": run_id}).to_list(200)
    )
    approval = await get_db().approvals.find_one({"user_id": user_id, "run_id": run_id})
    delivery = (
        await get_db().delivery_results.find_one({"user_id": user_id, "approval_id": approval["id"]})
        if approval
        else None
    )
    return {
        "historical": True,
        "trace": serialize_doc(trace),
        "spans": [serialize_doc(item) for item in spans],
        "evidence": [serialize_doc(item) for item in evidence],
        "approval": serialize_doc(approval),
        "delivery": serialize_doc(delivery),
    }


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


async def create_pull_request(user_id: str, slug_id: str, payload: CreatePullRequestRequest) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    approved = await get_db().approvals.find_one(
        {"sessionId": session["_id"], "action": "create_pull_request", "status": "approve"}
    )
    if not approved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pull request can only be created by an explicitly approved delivery plan.",
        )
    repo_path, _repo_name = await WorkspaceService().ensure_workspace(
        user_id,
        slug_id,
        session["repoUrl"],
        session.get("defaultBranch") or "main",
    )
    branch_name = session.get("branchName") or branch_name_from_prompt(session.get("title"))
    WorkspaceService().push_branch(repo_path, branch_name)
    owner, repo = repo_owner_name(session["repoUrl"])
    pr = await github_api(
        user_id,
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        json={
            "title": payload.title or f"Agent updates for {session['repoName']}",
            "body": payload.body or "Created by the LangGraph DevOps Agent.",
            "head": branch_name,
            "base": session.get("defaultBranch") or "main",
        },
    )
    return {
        "success": True,
        "url": pr["html_url"],
        "title": pr["title"],
        "body": pr.get("body") or "",
        "branch": branch_name,
        "timestamp": iso_now(),
    }
