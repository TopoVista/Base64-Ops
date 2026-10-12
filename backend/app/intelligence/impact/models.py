"""Typed records for conservative, revision-aware change impact analysis."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.graph.models import EpistemicStatus, GraphNodeType, GraphRelationType


class ChangeImpactStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ImpactRelationship(BaseModel):
    """One observed graph relationship used in a potential-impact path."""

    relation_type: GraphRelationType
    source_node_id: str
    target_node_id: str
    epistemic_status: EpistemicStatus


class ImpactTarget(BaseModel):
    """A graph node that may be affected by a changed repository file."""

    node_id: str
    node_type: GraphNodeType
    name: str
    path: str | None = None
    qualified_name: str | None = None
    distance: int = Field(ge=1)
    relationship_path: list[ImpactRelationship] = Field(default_factory=list)
    explanation: str


class ChangeImpactAnalysis(BaseModel):
    """Persisted output of a deterministic, bounded graph traversal.

    The internal tenancy/session fields are deliberately not part of the
    browser response schema below.  They exist only to scope persistence and
    retrieval.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    user_id: str
    session_id: str
    repository_id: str
    delivery_plan_id: str
    plan_base_sha: str
    graph_commit_sha: str | None = None
    status: ChangeImpactStatus
    changed_paths: list[str] = Field(default_factory=list)
    unmapped_paths: list[str] = Field(default_factory=list)
    directly_affected: list[ImpactTarget] = Field(default_factory=list)
    indirectly_affected: list[ImpactTarget] = Field(default_factory=list)
    related_tests: list[ImpactTarget] = Field(default_factory=list)
    related_workflows: list[ImpactTarget] = Field(default_factory=list)
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime


class ChangeImpactResponse(BaseModel):
    """Secret- and tenant-safe representation returned to a session owner."""

    id: str
    delivery_plan_id: str
    plan_base_sha: str
    graph_commit_sha: str | None = None
    status: ChangeImpactStatus
    changed_paths: list[str] = Field(default_factory=list)
    unmapped_paths: list[str] = Field(default_factory=list)
    directly_affected: list[ImpactTarget] = Field(default_factory=list)
    indirectly_affected: list[ImpactTarget] = Field(default_factory=list)
    related_tests: list[ImpactTarget] = Field(default_factory=list)
    related_workflows: list[ImpactTarget] = Field(default_factory=list)
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_analysis(cls, analysis: ChangeImpactAnalysis) -> ChangeImpactResponse:
        return cls.model_validate(
            analysis.model_dump(
                exclude={"user_id", "session_id", "repository_id"},
            )
        )
