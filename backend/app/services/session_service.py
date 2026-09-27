import asyncio
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.agent.graph import AgentGraph
from app.db.mongo import get_db
from app.observability.recorder import MongoTraceExporter, TraceRecorder
from app.schemas.session import ApprovalDecisionRequest, CreatePullRequestRequest, SessionChatRequest
from app.services.delivery_service import DeliveryService
from app.services.evidence_service import EvidenceService
from app.services.github_service import github_api
from app.services.rag_service import RagService
from app.services.redaction_service import RedactionService
from app.services.serializers import serialize_doc
from app.services.workspace_service import WorkspaceService, branch_name_from_prompt, repo_name_from_url
from app.utils.datetime import iso_now, utc_now
from app.utils.ids import new_id
from app.utils.sse import sse_event

agent_graph = AgentGraph()
redactor = RedactionService()


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


async def reindex_session(user_id: str, slug_id: str) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    db = get_db()
    await db.sessions.update_one({"_id": session["_id"]}, {"$set": {"indexStatus": "indexing", "indexError": None}})
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
        await db.sessions.update_one(
            {"_id": session["_id"]},
            {"$set": {"indexStatus": index_status, "indexError": reason, "indexedAt": utc_now()}},
        )
        return {"success": bool(result.get("indexed")), "status": index_status, "reason": reason, **result}
    except HTTPException as exc:
        reason = str(exc.detail)
    except Exception as exc:
        reason = index_failure_reason(exc)
    await db.sessions.update_one(
        {"_id": session["_id"]},
        {"$set": {"indexStatus": "failed", "indexError": reason, "indexedAt": utc_now()}},
    )
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
