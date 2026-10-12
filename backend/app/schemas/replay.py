"""Public, read-only contracts for historical run replay responses.

Replay records are assembled from several persisted collections.  These
models deliberately describe the small review surface the browser needs and
therefore provide a final allowlist even if a new field is later added to one
of those collection documents.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReplayPublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ReplaySession(ReplayPublicModel):
    slug_id: str | None = None
    repository: str | None = None


class ReplayRepository(ReplayPublicModel):
    headSha: str | None = None
    head_sha: str | None = None
    branch: str | None = None


class ReplayInvestigation(ReplayPublicModel):
    summary: str | None = None
    diagnosis: str | None = None
    status: str | None = None


class ReplayTimelineItem(ReplayPublicModel):
    label: str | None = None
    detail: str | None = None
    status: str | None = None
    timestamp: str | None = None


class ReplayToolResult(ReplayPublicModel):
    success: bool
    output: str = ""


class ReplayTrace(ReplayPublicModel):
    id: str | None = None
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    total_duration_ms: int | None = None
    model_usage: dict[str, Any] = Field(default_factory=dict)
    error_count: int = 0


class ReplaySpan(ReplayPublicModel):
    id: str | None = None
    parent_span_id: str | None = None
    name: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    status: str | None = None
    safe_input_summary: dict[str, Any] = Field(default_factory=dict)
    safe_output_summary: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None


class ReplayFileChange(ReplayPublicModel):
    path: str | None = None
    change_type: str | None = None
    original_hash: str | None = None
    proposed_hash: str | None = None
    unified_diff: str | None = None
    # A replay may read a legacy record created before patch-size ceilings
    # existed. The browser must never mistake a bounded preview for the full
    # exact delivery diff.
    diff_truncated: bool = False


class ReplayValidationStep(ReplayPublicModel):
    id: str | None = None
    kind: str | None = None
    description: str | None = None
    required: bool = True


class ReplayDeliveryPlan(ReplayPublicModel):
    id: str | None = None
    title: str | None = None
    rationale: str | None = None
    risk_level: str | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    files: list[ReplayFileChange] = Field(default_factory=list)
    validation_steps: list[ReplayValidationStep] = Field(default_factory=list)


class ReplayApproval(ReplayPublicModel):
    id: str | None = None
    status: str | None = None
    risk_level: str | None = None
    diff_hash: str | None = None
    created_at: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None


class ReplayValidationResult(ReplayPublicModel):
    step_id: str | None = None
    status: str | None = None
    summary: str = ""
    duration_ms: int | None = None
    evidence_id: str | None = None


class ReplayDeliveryResult(ReplayPublicModel):
    id: str | None = None
    status: str | None = None
    failure_stage: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    validation_results: list[ReplayValidationResult] = Field(default_factory=list)
    created_at: str | None = None


class ReplayFailedJob(ReplayPublicModel):
    name: str | None = None
    failed_step: str | None = None


class ReplayCIInvestigation(ReplayPublicModel):
    run_id: int | None = None
    workflow_name: str | None = None
    head_sha: str | None = None
    current_head_sha: str | None = None
    run_is_historical: bool | None = None
    applicability: str | None = None
    applicability_reason: str | None = None
    failed_jobs: list[ReplayFailedJob] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    workflow_evidence_ids: list[str] = Field(default_factory=list)
    changed_file_evidence_ids: list[str] = Field(default_factory=list)
    relevant_paths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReplayEvidence(ReplayPublicModel):
    id: str | None = None
    path: str = "Unknown source"
    source: str | None = None
    source_type: str = "repo_file"
    retrieval_method: str = "evidence"
    score: float | None = None
    commit_sha: str | None = None
    state: str | None = None
    truncated: bool = False


class ReplayImpactTarget(ReplayPublicModel):
    """A browser-safe potential-impact item.

    Relationship node IDs remain an internal graph traversal detail; replay
    exposes only the observed relation labels that explain why an item was
    selected.
    """

    node_type: str | None = None
    name: str | None = None
    path: str | None = None
    qualified_name: str | None = None
    distance: int | None = None
    relations: list[str] = Field(default_factory=list)
    explanation: str | None = None


class ReplayChangeImpact(ReplayPublicModel):
    id: str | None = None
    status: Literal["available", "unavailable"] | None = None
    plan_base_sha: str | None = None
    graph_commit_sha: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    unmapped_paths: list[str] = Field(default_factory=list)
    directly_affected: list[ReplayImpactTarget] = Field(default_factory=list)
    indirectly_affected: list[ReplayImpactTarget] = Field(default_factory=list)
    related_tests: list[ReplayImpactTarget] = Field(default_factory=list)
    related_workflows: list[ReplayImpactTarget] = Field(default_factory=list)
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list)
    created_at: str | None = None


class HistoricalRun(ReplayPublicModel):
    """Bounded, browser-safe metadata used to select a historical replay."""

    run_id: str
    trace_id: str | None = None
    created_at: str | None = None
    status: str = "completed"
    title: str = "Historical investigation"
    kind: Literal["ci", "delivery", "investigation"] = "investigation"
    evidence_count: int = 0
    has_trace: bool = False
    ci_run_id: int | None = None
    delivery_status: str | None = None
    has_change_impact: bool = False


class SessionRunHistoryResponse(ReplayPublicModel):
    runs: list[HistoricalRun] = Field(default_factory=list)
    limit: int


class RunReplayResponse(ReplayPublicModel):
    run_id: str
    # This endpoint is intentionally a historical inspection surface. Keeping
    # these as literals makes an accidental mutable/re-executing response a
    # contract error rather than a browser-visible state change.
    historical: Literal[True]
    replay_mode: Literal["read_only"]
    session: ReplaySession
    repository: ReplayRepository = Field(default_factory=ReplayRepository)
    investigation: ReplayInvestigation | None = None
    timeline: list[ReplayTimelineItem] = Field(default_factory=list)
    trace: ReplayTrace | None = None
    spans: list[ReplaySpan] = Field(default_factory=list)
    delivery_plan: ReplayDeliveryPlan | None = None
    approval: ReplayApproval | None = None
    delivery_result: ReplayDeliveryResult | None = None
    ci_investigation: ReplayCIInvestigation | None = None
    tool_result: ReplayToolResult | None = None
    evidence_count: int = 0
    evidence: list[ReplayEvidence] = Field(default_factory=list)
    change_impact: ReplayChangeImpact | None = None
    limitations: list[str] = Field(default_factory=list)
