from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProposedFileChange(BaseModel):
    path: str
    change_type: Literal["create", "modify", "delete"]
    original_hash: str | None
    proposed_hash: str | None
    unified_diff: str
    proposed_content: str | None = Field(default=None, exclude=True)


class ValidationStep(BaseModel):
    id: str
    kind: Literal["test", "lint", "typecheck", "docker", "config", "custom"]
    command: str | None = None
    description: str
    required: bool = True


class ValidationResult(BaseModel):
    step_id: str
    status: Literal["passed", "failed", "skipped", "error"]
    summary: str
    duration_ms: int | None = None
    evidence_id: str | None = None


class DeliveryPlan(BaseModel):
    id: str
    user_id: str
    session_id: str
    run_id: str
    repository_id: str
    base_branch: str
    base_sha: str
    title: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    files: list[ProposedFileChange]
    validation_steps: list[ValidationStep]
    risk_level: Literal["low", "medium", "high", "critical"]
    risk_reasons: list[str] = Field(default_factory=list)
    created_at: datetime


class ApprovalRecord(BaseModel):
    id: str
    user_id: str
    session_id: str
    run_id: str
    repository_id: str
    delivery_plan_id: str
    base_branch: str
    base_sha: str
    action_type: str
    canonical_arguments: dict = Field(default_factory=dict)
    diff_hash: str
    approval_hash: str
    risk_level: str
    status: Literal["pending", "approved", "rejected", "expired", "invalidated", "executing", "executed", "failed"]
    created_at: datetime
    approved_at: datetime | None = None
    executed_at: datetime | None = None


class DeliveryResult(BaseModel):
    id: str
    delivery_plan_id: str
    approval_id: str
    repository_id: str
    branch_name: str | None = None
    commit_sha: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    validation_results: list[ValidationResult] = Field(default_factory=list)
    status: Literal["success", "partial", "failed"]
    failure_stage: str | None = None
    created_at: datetime
