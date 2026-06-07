from typing import Any, Literal, TypedDict

RiskLevel = Literal["safe", "approval_required"]


class AgentAction(TypedDict, total=False):
    name: str
    args: dict[str, Any]
    risk: RiskLevel
    summary: str


class AgentState(TypedDict, total=False):
    user_id: str
    session_id: str
    slug_id: str
    repo_url: str
    repo_name: str
    repo_path: str
    default_branch: str
    branch_name: str
    prompt: str
    sources: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    action: AgentAction | None
    approval: dict[str, Any] | None
    tool_result: dict[str, Any] | None
    final: str
    error: str | None
