from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceSourceType = Literal[
    "repo_file",
    "git_diff",
    "git_commit",
    "ci_log",
    "runtime_log",
    "metric",
    "trace",
    "runbook",
    "screenshot",
    "tool_result",
    "security_finding",
]


class EvidenceItem(BaseModel):
    id: str
    user_id: str
    session_id: str
    run_id: str
    source_type: EvidenceSourceType
    repository_id: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    path: str | None = None
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    title: str
    excerpt: str
    source_timestamp: datetime | None = None
    retrieved_at: datetime
    retrieval_score: float | None = None
    confidence: float | None = None
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    id: str
    title: str
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    status: Literal["candidate", "supported", "weakened", "rejected", "confirmed"] = "candidate"


class ActionProposal(BaseModel):
    id: str
    title: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"
    validation_plan: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class InvestigationResult(BaseModel):
    run_id: str
    status: Literal["complete", "incomplete", "awaiting_approval", "failed"]
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    proposal: ActionProposal | None = None
    confidence: float = Field(ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)
