"""Browser-safe schemas for the bounded Engineering Intelligence APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.intelligence.graph.models import (
    EngineeringGraphSnapshot,
    EpistemicStatus,
    GraphCoverage,
    GraphDiscoveryMethod,
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationType,
)
from app.intelligence.impact.models import ChangeImpactResponse


class GraphNodePublic(BaseModel):
    id: str
    logical_id: str
    version_id: str
    type: GraphNodeType
    name: str
    qualified_name: str | None = None
    path: str | None = None
    language: str | None = None
    commit_sha: str
    branch: str | None = None
    attributes: dict = Field(default_factory=dict)
    content_hash: str | None = None
    discovery_method: GraphDiscoveryMethod
    verification_status: EpistemicStatus

    @classmethod
    def from_node(cls, node: GraphNode) -> GraphNodePublic:
        return cls.model_validate(
            node.model_dump(
                exclude={
                    "tenant_id",
                    "repository_id",
                    "source_evidence_ids",
                    "first_observed_at",
                    "last_observed_at",
                    "created_at",
                    "updated_at",
                }
            )
        )


class GraphEdgePublic(BaseModel):
    id: str
    source_node_id: str
    target_node_id: str
    relation_type: GraphRelationType
    source_version: str
    target_version: str
    discovery_method: GraphDiscoveryMethod
    confidence: float | None = None
    epistemic_status: EpistemicStatus
    validity_scope: str
    properties: dict = Field(default_factory=dict)

    @classmethod
    def from_edge(cls, edge: GraphEdge) -> GraphEdgePublic:
        return cls.model_validate(edge.model_dump(exclude={"repository_id", "evidence_ids", "observed_at"}))


class GraphSnapshotStatus(BaseModel):
    available: bool
    snapshot_id: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    built_at: datetime | None = None
    node_count: int = 0
    edge_count: int = 0
    coverage: GraphCoverage | None = None
    limitations: list[str] = Field(default_factory=list)

    @classmethod
    def from_snapshot(cls, snapshot: EngineeringGraphSnapshot | None) -> GraphSnapshotStatus:
        if snapshot is None:
            return cls(
                available=False,
                limitations=["No engineering graph has been built for this repository revision yet."],
            )
        return cls(
            available=True,
            snapshot_id=snapshot.id,
            commit_sha=snapshot.commit_sha,
            branch=snapshot.branch,
            built_at=snapshot.built_at,
            node_count=len(snapshot.nodes),
            edge_count=len(snapshot.edges),
            coverage=snapshot.coverage,
            limitations=snapshot.coverage.limitations,
        )


class GraphNodeDetailResponse(BaseModel):
    snapshot: GraphSnapshotStatus
    node: GraphNodePublic
    nodes: list[GraphNodePublic]
    edges: list[GraphEdgePublic]
    truncated: bool = False


# Kept here with the other browser-safe intelligence contracts so routes do
# not leak the persistence model's tenant and repository identifiers.
__all__ = [
    "ChangeImpactResponse",
    "GraphEdgePublic",
    "GraphNodeDetailResponse",
    "GraphNodePublic",
    "GraphSnapshotStatus",
]
