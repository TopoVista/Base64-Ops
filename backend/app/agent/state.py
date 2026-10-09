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
    patch_target_path: str | None
    run_id: str
    trace_id: str | None
    # First-party focused workflows (such as the Code workbench safe-repair
    # action) use the investigation graph but do not need a conversational
    # model-written closing message.  Keeping this explicit prevents an
    # unnecessary second provider request after a structured patch proposal.
    workbench_mode: bool
    # Only the first-party Code workbench can set this server-side flag. It
    # selects the existing constrained proposal/approval path and never
    # authorizes applying a patch.
    force_delivery_plan: bool
    model_usage: dict[str, Any]
    # One graph run can use more than one provider call (for example, a
    # structured patch proposal followed by the final evidence-backed answer).
    # Retain only provider-reported usage summaries, never prompts or outputs,
    # so tenant metering cannot silently omit an earlier call.
    model_usage_events: list[dict[str, Any]]
    repo_commit_sha: str | None
    repository_map: dict[str, Any]
    context_pack: dict[str, Any]
    sources: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    investigation: dict[str, Any]
    delivery_plan_id: str | None
    delivery_plan: dict[str, Any] | None
    validation_results: list[dict[str, Any]]
    approval_id: str | None
    delivery_result_id: str | None
    change_surfaces: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    action: AgentAction | None
    approval: dict[str, Any] | None
    tool_result: dict[str, Any] | None
    final: str
    error: str | None
    operational_memory: list[dict[str, Any]]
    ci_request_intent: dict[str, Any]
    ci_run_id: int | None
    ci_context: dict[str, Any]
    ci_evidence_ids: list[str]
    failed_run_sha: str | None
    current_head_sha: str | None
    ci_applicability: str | None
