from typing import Any

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    id: str
    name: str
    category: str
    user_request: str
    repository_fixture: str
    expected_evidence_paths: list[str] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    prohibited_tools: list[str] = Field(default_factory=list)
    expected_requires_approval: bool | None = None
    expected_diagnosis_contains: list[str] = Field(default_factory=list)
    expected_behavior: list[str] = Field(default_factory=list)
    expected_changed_files: list[str] = Field(default_factory=list)
    expected_change_surfaces: list[str] = Field(default_factory=list)
    should_reject: bool = False
    expected_min_risk: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalResult(BaseModel):
    case_id: str
    passed: bool
    diagnosis_correct: bool | None = None
    evidence_recall: float | None = None
    citation_precision: float | None = None
    tool_selection_correct: bool | None = None
    approval_policy_correct: bool | None = None
    unsupported_claim_count: int = 0
    prohibited_tool_calls: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    token_usage: dict[str, Any] | None = None
    expected_changed_files: list[str] = Field(default_factory=list)
    actual_changed_files: list[str] = Field(default_factory=list)
    unexpected_changed_files: list[str] = Field(default_factory=list)
    patch_applies: bool | None = None
    validation_passes: bool | None = None
    unsafe_edit_rejected: bool | None = None
    ci_category_correct: bool | None = None
    ci_applicability_correct: bool | None = None
    ci_secret_leak_count: int | None = None
    ci_duplicate_patch_prevented: bool | None = None
    selection_precision: float | None = None
    selection_recall: float | None = None
    errors: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class LiveDeliveryTestResult(BaseModel):
    repository_url: str
    test_branch: str
    pr_number: int | None = None
    pr_url: str | None = None
    status: str
    diff_verified: bool = False
    cleaned_up: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class LiveEvalReport(BaseModel):
    mode: str = "live"
    enabled: bool = True
    deterministic_passed: int = 0
    live_passed: int = 0
    live_failed: int = 0
    github_delivery: LiveDeliveryTestResult | None = None
    total_token_usage: dict[str, int] = Field(default_factory=dict)
    results: list[EvalResult] = Field(default_factory=list)
