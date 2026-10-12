"""Typed, provenance-first records for the Engineering Intelligence graph."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphNodeType(StrEnum):
    REPOSITORY = "repository"
    FILE = "file"
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    API_ENDPOINT = "api_endpoint"
    WORKFLOW = "workflow"
    CI_JOB = "ci_job"
    TEST = "test"
    PACKAGE = "package"
    CONFIGURATION = "configuration"
    DOCKER_SERVICE = "docker_service"


class GraphRelationType(StrEnum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    EXPOSES = "exposes"
    TESTS = "tests"
    DEPENDS_ON = "depends_on"
    CONFIGURES = "configures"
    EXECUTES = "executes"


class EpistemicStatus(StrEnum):
    """How strongly the graph record is known, rather than inferred."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class GraphDiscoveryMethod(StrEnum):
    PYTHON_AST = "python_ast"
    STATIC_IMPORT = "static_import"
    YAML_SAFE_PARSE = "yaml_safe_parse"
    MANIFEST_PARSE = "manifest_parse"
    FILESYSTEM = "filesystem"


class GraphNode(BaseModel):
    """A versioned graph node with a stable logical identity."""

    model_config = ConfigDict(extra="forbid")

    id: str
    logical_id: str
    version_id: str
    tenant_id: str
    repository_id: str
    type: GraphNodeType
    name: str
    qualified_name: str | None = None
    path: str | None = None
    language: str | None = None
    commit_sha: str
    branch: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    source_evidence_ids: list[str] = Field(default_factory=list)
    discovery_method: GraphDiscoveryMethod
    verification_status: EpistemicStatus
    first_observed_at: datetime
    last_observed_at: datetime
    created_at: datetime
    updated_at: datetime


class GraphEdge(BaseModel):
    """A versioned relationship whose epistemic strength is explicit."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source_node_id: str
    target_node_id: str
    relation_type: GraphRelationType
    repository_id: str
    source_version: str
    target_version: str
    evidence_ids: list[str] = Field(default_factory=list)
    discovery_method: GraphDiscoveryMethod
    confidence: float | None = Field(default=None, ge=0, le=1)
    epistemic_status: EpistemicStatus
    observed_at: datetime
    validity_scope: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphCoverage(BaseModel):
    """Honest representation of what static parsers did and did not inspect."""

    scanned_files: int = Field(ge=0)
    parsed_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    unsupported_files: int = Field(ge=0)
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list)


class EngineeringGraphSnapshot(BaseModel):
    """An immutable graph build for exactly one repository revision."""

    model_config = ConfigDict(extra="forbid")

    id: str
    user_id: str
    session_id: str
    repository_id: str
    commit_sha: str
    branch: str | None = None
    built_at: datetime
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    coverage: GraphCoverage
    schema_version: int = 1


# Intentional public aliases: callers use engineering-oriented names while the
# persisted model stays concise and compatible with the existing codebase.
EngineeringGraphNode = GraphNode
EngineeringGraphEdge = GraphEdge
