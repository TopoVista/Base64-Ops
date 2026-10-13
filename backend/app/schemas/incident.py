"""Typed, bounded representations for automated CI incidents."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    QUEUED = "queued"
    INVESTIGATING = "investigating"
    EVIDENCE_COLLECTED = "evidence_collected"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    IGNORED = "ignored"
    GROUPED = "grouped"


class EvidenceStrength(StrEnum):
    INSUFFICIENT = "insufficient"
    OBSERVED = "observed"
    CORRELATED = "correlated"
    SUPPORTED = "supported"


class IncidentDiagnosis(BaseModel):
    """Evidence-scoped diagnosis stored with an automated CI incident."""

    schema_version: Literal["incident-diagnosis-v1"] = "incident-diagnosis-v1"
    status: Literal["insufficient", "candidate", "supported"]
    summary: str
    evidence_strength: EvidenceStrength
    failure_categories: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradictory_evidence_ids: list[str] = Field(default_factory=list)
    relevant_paths: list[str] = Field(default_factory=list)
    historical_context: dict[str, Any] | None = None
    applicability: str | None = None
    recommended_next_action: str


class RepositoryAutomationSettingsRequest(BaseModel):
    """Browser-authenticated opt-in; repository identity is server-derived."""

    enabled: bool = False
    workflow_names: list[str] = Field(default_factory=list, max_length=30)
    include_branches: list[str] = Field(default_factory=list, max_length=30)
    exclude_branches: list[str] = Field(default_factory=list, max_length=30)
    daily_investigation_limit: int = Field(default=5, ge=1, le=25)
    # This preference only permits a neutral, redacted GitHub Check after
    # evidence collection. It is inert unless the deployment has enabled a
    # GitHub App with Checks: write and the repository has a verified App
    # installation binding.
    github_check_reporting_enabled: bool = False


class RepositoryAutomationSettingsResponse(BaseModel):
    id: str
    session_id: str
    repository_id: str
    enabled: bool
    workflow_names: list[str] = Field(default_factory=list)
    include_branches: list[str] = Field(default_factory=list)
    exclude_branches: list[str] = Field(default_factory=list)
    daily_investigation_limit: int
    github_check_reporting_enabled: bool = False
    updated_at: datetime


class CandidateVerificationSettingsRequest(BaseModel):
    """Opt-in binding for a reviewed repository-owned verification workflow."""

    enabled: bool = False
    installation_id: str | None = Field(default=None, max_length=19)
    workflow_path: str | None = Field(default=None, max_length=220)


class CandidateVerificationSettingsResponse(BaseModel):
    id: str
    session_id: str
    repository_id: str
    repository_full_name: str | None = None
    enabled: bool
    workflow_path: str | None = None
    configured: bool
    updated_at: datetime


class IncidentFeedbackRequest(BaseModel):
    """A user correction is an annotation, never evidence replacement."""

    disposition: Literal["helpful", "incorrect", "needs_more_evidence"]
    correction: str = Field(default="", max_length=2_000)


class FlakyTestObservationSummary(BaseModel):
    """A bounded public observation, not a test-execution command."""

    id: str
    repository_id: str
    workflow_key: str
    test_identifier: str
    head_sha: str
    assessment: Literal["insufficient_evidence", "suspected_flakiness", "reproducible_regression"]
    same_sha_failure_count: int = 0
    same_sha_success_count: int = 0
    limitations: list[str] = Field(default_factory=list)
    updated_at: datetime


class IncidentSummary(BaseModel):
    id: str
    repository_id: str
    repository_name: str
    workflow_run_id: int
    workflow_name: str | None = None
    branch: str | None = None
    head_sha: str
    status: IncidentStatus
    failure_categories: list[str] = Field(default_factory=list)
    evidence_strength: EvidenceStrength = EvidenceStrength.INSUFFICIENT
    diagnosis_summary: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    incomplete: bool = False
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
