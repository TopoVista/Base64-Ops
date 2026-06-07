import asyncio
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.agent.graph import AgentGraph
from app.db.mongo import get_db
from app.schemas.session import ApprovalDecisionRequest, CreatePullRequestRequest, SessionChatRequest
from app.services.github_service import github_api
from app.services.rag_service import RagService
from app.services.serializers import serialize_doc
from app.services.workspace_service import WorkspaceService, branch_name_from_prompt, repo_name_from_url
from app.utils.datetime import iso_now, utc_now
from app.utils.ids import new_id
from app.utils.sse import sse_event

agent_graph = AgentGraph()


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
    sessions = (
        await db.sessions.find(query)
        .sort("createdAt", -1)
        .skip(skip)
        .limit(page_size)
        .to_list(page_size)
    )
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
    return {
        "session": serialize_doc(session),
        "messages": [serialize_doc(message) for message in messages],
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
    session = await get_or_create_session(user_id, payload, prompt)
    await append_message(session["_id"], "user", prompt)
    yield sse_event("session.updated", {"session": serialize_doc(session)})
    yield sse_event("tool.started", {"name": "langgraph", "label": "Starting LangGraph run"})

    result = await agent_graph.run(
        {
            "user_id": user_id,
            "session_id": session["_id"],
            "slug_id": session["slugId"],
            "repo_url": session["repoUrl"],
            "default_branch": session.get("defaultBranch") or "main",
            "branch_name": session.get("branchName") or branch_name_from_prompt(prompt),
            "prompt": prompt,
        }
    )

    interrupt = extract_interrupt(result)
    if interrupt:
        approval = {
            **interrupt,
            "sessionId": session["_id"],
            "slugId": session["slugId"],
            "status": "pending",
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        await get_db().approvals.update_one({"id": approval["id"]}, {"$set": approval}, upsert=True)
        yield sse_event("approval.requested", {"approval": serialize_doc(approval)})
        return

    sources = result.get("sources", [])
    if sources:
        yield sse_event("rag.sources", {"sources": sources})
    for item in result.get("timeline", []):
        yield sse_event("tool.completed", item)

    final = result.get("final", "")
    assistant = await append_message(session["_id"], "assistant", final, sources)
    for chunk in chunk_for_stream(final):
        yield sse_event("message.delta", {"id": assistant["id"], "delta": chunk})
        await asyncio.sleep(0)
    yield sse_event("message.completed", {"message": serialize_doc(assistant)})


def chunk_for_stream(text: str, size: int = 80) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


async def decide_approval(user_id: str, slug_id: str, approval_id: str, payload: ApprovalDecisionRequest) -> dict:
    db = get_db()
    session = await db.sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    approval = await db.approvals.find_one({"id": approval_id, "sessionId": session["_id"]})
    if not approval:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.get("status") != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")
    decision = payload.model_dump()
    await db.approvals.update_one(
        {"id": approval_id},
        {"$set": {"status": payload.decision, "decision": decision, "updatedAt": utc_now()}},
    )
    if payload.decision == "reject":
        message = await append_message(session["_id"], "assistant", "Approval rejected. No action was run.")
        return {"approval": {**approval, "status": "reject"}, "message": serialize_doc(message)}
    result = await agent_graph.resume(slug_id, decision)
    final = result.get("final", "Approved action completed.")
    message = await append_message(session["_id"], "assistant", final, result.get("sources", []))
    return {"approval": {**approval, "status": payload.decision}, "message": serialize_doc(message), "result": result}


async def reindex_session(user_id: str, slug_id: str) -> dict:
    session = await get_db().sessions.find_one({"userId": user_id, "slugId": slug_id})
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    repo_path, _repo_name = await WorkspaceService().ensure_workspace(
        user_id,
        slug_id,
        session["repoUrl"],
        session.get("defaultBranch") or "main",
    )
    result = await RagService().index_workspace(session, repo_path)
    return {"success": True, **result}


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
