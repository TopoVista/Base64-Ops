from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.policy import action_requires_approval, choose_action
from app.agent.prompts import SYSTEM_PROMPT, build_answer_prompt
from app.agent.state import AgentState
from app.agent.tools import DevOpsToolbox
from app.core.config import get_settings
from app.services.rag_service import RagService
from app.services.workspace_service import WorkspaceService
from app.utils.ids import new_id


class AgentGraph:
    def __init__(self) -> None:
        self.workspace = WorkspaceService()
        self.rag = RagService()
        self.tools = DevOpsToolbox()
        self.graph = self._build_graph()

    async def run(self, state: AgentState) -> AgentState:
        config = {"configurable": {"thread_id": state["slug_id"], "checkpoint_ns": ""}}
        result = await self.graph.ainvoke(state, config=config)
        return result

    async def resume(self, slug_id: str, decision: dict[str, Any]) -> AgentState:
        config = {"configurable": {"thread_id": slug_id, "checkpoint_ns": ""}}
        return await self.graph.ainvoke(Command(resume=decision), config=config)

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("load_context", self._load_context)
        builder.add_node("retrieve", self._retrieve)
        builder.add_node("plan", self._plan)
        builder.add_node("approval", self._approval)
        builder.add_node("execute", self._execute)
        builder.add_node("respond", self._respond)

        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "retrieve")
        builder.add_edge("retrieve", "plan")
        builder.add_conditional_edges(
            "plan",
            lambda state: "approval" if action_requires_approval(state.get("action")) else "execute",
            {"approval": "approval", "execute": "execute"},
        )
        builder.add_edge("approval", "execute")
        builder.add_edge("execute", "respond")
        builder.add_edge("respond", END)
        return builder.compile(checkpointer=self._checkpointer())

    def _checkpointer(self):
        settings = get_settings()
        if settings.mongo_uri:
            try:
                from langgraph.checkpoint.mongodb import MongoDBSaver

                saver = MongoDBSaver.from_conn_string(settings.mongo_uri, settings.mongo_db_name)
                setup = getattr(saver, "setup", None)
                if callable(setup):
                    setup()
                return saver
            except Exception:
                return MemorySaver()
        return MemorySaver()

    async def _load_context(self, state: AgentState) -> AgentState:
        repo_path, repo_name = await self.workspace.ensure_workspace(
            state["user_id"],
            state["slug_id"],
            state["repo_url"],
            state.get("default_branch") or "main",
        )
        branch_name = state.get("branch_name") or f"agent/{state['slug_id'][:10]}"
        await self.workspace.ensure_branch(repo_path, branch_name)
        return {
            "repo_path": str(repo_path),
            "repo_name": repo_name,
            "branch_name": branch_name,
            "timeline": [
                {
                    "id": new_id("evt_"),
                    "label": "Workspace ready",
                    "detail": f"{repo_name} on {branch_name}",
                    "status": "completed",
                }
            ],
        }

    async def _retrieve(self, state: AgentState) -> AgentState:
        sources = await self.rag.retrieve(state["session_id"], state["prompt"], limit=6)
        return {
            "sources": sources,
            "timeline": state.get("timeline", [])
            + [
                {
                    "id": new_id("evt_"),
                    "label": "RAG retrieval",
                    "detail": f"{len(sources)} source(s) retrieved",
                    "status": "completed",
                }
            ],
        }

    async def _plan(self, state: AgentState) -> AgentState:
        action = choose_action(state["prompt"])
        return {
            "action": action,
            "timeline": state.get("timeline", [])
            + [
                {
                    "id": new_id("evt_"),
                    "label": "Action selected",
                    "detail": action["summary"],
                    "status": "completed",
                    "risk": action["risk"],
                }
            ],
        }

    async def _approval(self, state: AgentState) -> Command:
        action = state["action"] or {}
        approval_payload = {
            "id": new_id("apr_"),
            "action": action.get("name"),
            "summary": action.get("summary"),
            "args": action.get("args", {}),
            "risk": "approval_required",
            "blastRadius": "Can modify repository state or trigger DevOps side effects.",
            "rollback": "Reject approval, revert workspace changes with git, or avoid pushing the branch.",
        }
        decision = interrupt(approval_payload)
        if isinstance(decision, dict) and decision.get("decision") == "reject":
            return Command(
                goto="respond",
                update={
                    "approval": {**approval_payload, "decision": "reject"},
                    "tool_result": {"success": False, "output": "Human rejected the action."},
                },
            )
        edited_args = decision.get("editedArgs") if isinstance(decision, dict) else None
        if edited_args:
            action["args"] = edited_args
        return Command(
            goto="execute",
            update={"approval": {**approval_payload, "decision": "approve"}, "action": action},
        )

    async def _execute(self, state: AgentState) -> AgentState:
        action = state.get("action")
        if not action:
            return {"tool_result": {"success": True, "output": "No tool action required."}}
        if action_requires_approval(action) and state.get("approval", {}).get("decision") != "approve":
            return {"tool_result": {"success": False, "output": "Action was not approved."}}
        if action_requires_approval(action):
            result = self.tools.run_approved_action(
                action["name"],
                state["repo_path"],
                state.get("branch_name", ""),
                action.get("args", {}),
            )
        else:
            result = self.tools.run_safe_action(action["name"], state["repo_path"], action.get("args", {}))
        return {
            "tool_result": result,
            "timeline": state.get("timeline", [])
            + [
                {
                    "id": new_id("evt_"),
                    "label": action["name"],
                    "detail": result.get("output", "")[:240],
                    "status": "completed" if result.get("success") else "failed",
                }
            ],
        }

    async def _respond(self, state: AgentState) -> AgentState:
        settings = get_settings()
        prompt = build_answer_prompt(
            state["prompt"],
            state.get("sources", []),
            state.get("tool_result"),
        )
        if settings.openai_api_key:
            model = ChatOpenAI(
                model=settings.chat_model,
                api_key=settings.openai_api_key,
                temperature=0.2,
            )
            response = await model.ainvoke(
                [
                    ("system", SYSTEM_PROMPT),
                    ("user", prompt),
                ]
            )
            final = str(response.content)
        else:
            result = state.get("tool_result") or {}
            source_count = len(state.get("sources", []))
            final = (
                f"Inspected the selected repository with {source_count} retrieved source(s).\n\n"
                f"Tool result: {result.get('output', 'No tool output available')}\n\n"
                "Set `OPENAI_API_KEY` to enable full LangGraph LLM reasoning over this context."
            )
        return {"final": final}
